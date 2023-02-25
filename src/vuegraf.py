#!/venv/bin/python python

import datetime
import json
import signal
import sys
import time
import traceback
from threading import Event
import logging

# MQTT
import paho.mqtt.publish as publish

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger()


def handleExit(signum, frame):
    global running
    logger.error('Caught exit signal')
    running = False
    pauseEvent.set()

def getConfigValue(key, defaultValue):
    if key in config:
        return config[key]
    return defaultValue

def populateDevices(account):
    deviceIdMap = {}
    account['deviceIdMap'] = deviceIdMap
    channelIdMap = {}
    account['channelIdMap'] = channelIdMap
    devices = account['vue'].get_devices()
    for device in devices:
        device = account['vue'].populate_device_properties(device)
        deviceIdMap[device.device_gid] = device
        for chan in device.channels:
            key = "{}-{}".format(device.device_gid, chan.channel_num)
            if chan.name is None and chan.channel_num == '1,2,3':
                chan.name = device.device_name
            channelIdMap[key] = chan
            logger.info("Discovered new channel: {} ({})".format(chan.name, chan.channel_num))


def extractDataPoints(device):
    excludedDetailChannelNumbers = ['Balance', 'TotalUsage']
    minutesInAnHour = 60
    secondsInAMinute = 60
    wattsInAKw = 1000
    usageDataPoints = []
    cols = ['time','device_id','total']

    if detailedEnabled:
        length_timestamps = int((stopTime - detailedStartTime).total_seconds())
        timestamps = [(detailedStartTime + datetime.timedelta(seconds=s)).isoformat() for s in range(length_timestamps)]
        deviceId = [device.device_gid for _ in range(length_timestamps)]
        usageDataPoints.append(timestamps)
        usageDataPoints.append(deviceId)
        for chanNum, chan in device.channels.items():
            if chan.name == 'Balance': continue
            if chanNum != '1,2,3':
                cols.append(chan.name.replace(' ','_') + '_' + chanNum)
                # cols.append('chan' + chanNum)
            usage, usage_start_time = account['vue'].get_chart_usage(chan, detailedStartTime, stopTime, scale=Scale.SECOND.value, unit=Unit.KWH.value)
            usages = [float(secondsInAMinute * minutesInAnHour * wattsInAKw) * kwhUsage for kwhUsage in usage]
            usageDataPoints.append(usages)

    else:
        usageDataPoints.append([stopTime.isoformat()])
        usageDataPoints.append([device.device_gid])
        for chanNum, chan in device.channels.items():
            if chan.name == 'Balance': continue
            if chanNum != '1,2,3':
                cols.append(chan.name.replace(' ','_') + '_' + chanNum)
                # cols.append('chan' + chanNum)
            usageDataPoints.append([float(minutesInAnHour * wattsInAKw) * chan.usage])

    return cols, usageDataPoints

def submitDataPoints(usageDataPoints, cols, broker):
    msgs = []
    for i, v in enumerate(usageDataPoints[0]):
        payload = {}
        for j, data in enumerate(usageDataPoints):
            try:
                payload[cols[j]] = data[i]
            except Exception as e:
                logger.error(f'Channel: {cols[j]}; timeLength: {len(usageDataPoints[0])}; dataLength: {len(data)}')
        msgs.append({'topic': 'home/energy', 'payload': json.dumps(payload)})
    publish.multiple(msgs, hostname=broker)

startupTime = datetime.datetime.utcnow()
try:
    if len(sys.argv) != 2:
        print('Usage: python {} <config-file>'.format(sys.argv[0]))
        sys.exit(1)

    configFilename = sys.argv[1]
    config = {}
    with open(configFilename) as configFile:
        config = json.load(configFile)

    running = True

    signal.signal(signal.SIGINT, handleExit)
    signal.signal(signal.SIGHUP, handleExit)

    pauseEvent = Event()

    intervalSecs=getConfigValue("updateIntervalSecs", 60)
    detailedIntervalSecs=getConfigValue("detailedIntervalSecs", 30) # default is to pull every seconds data
    lagSecs=getConfigValue("lagSecs", 5)
    broker = getConfigValue("mqttBroker", "192.168.1.1")
    detailedStartTime = startupTime

    while running:
        now = datetime.datetime.utcnow()
        stopTime = now - datetime.timedelta(seconds=lagSecs)
        detailedEnabled = (stopTime - detailedStartTime).total_seconds() >= detailedIntervalSecs

        for account in config["accounts"]:
            if 'vue' not in account:
                account['vue'] = PyEmVue()
                account['vue'].login(username=account['email'], password=account['password'])
                logger.info('Login completed')
                populateDevices(account)

            try:
                deviceGids = list(account['deviceIdMap'].keys())
                usages = account['vue'].get_device_list_usage(deviceGids, stopTime, scale=Scale.MINUTE.value, unit=Unit.KWH.value)
                if usages is not None:
                    for gid, device in usages.items():
                        cols, usageDataPoints = extractDataPoints(device)

                    logger.info(f'Submitting datapoints to database; account="{account["name"]}"; channels={len(usageDataPoints)}, points={len(usageDataPoints[0])}')
                    submitDataPoints(usageDataPoints, cols, broker)
            except Exception as e:
                print(e)
                logger.error('Failed to record new usage data: {}'.format(sys.exc_info())) 
                traceback.print_exc()

        if detailedEnabled:
            detailedStartTime = stopTime + datetime.timedelta(seconds=1)

        pauseEvent.wait(intervalSecs)

    logger.info('Finished')
except Exception as e:
    logger.error(e) 
