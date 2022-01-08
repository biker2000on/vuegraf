#!/venv/bin/python python

import datetime
import json
import signal
import sys
import time
import traceback
from threading import Event

# Timescale
import psycopg2
from pgcopy import CopyManager

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

# flush=True helps when running in a container without a tty attached
# (alternatively, "python -u" or PYTHONUNBUFFERED will help here)
def log(level, msg):
    now = datetime.datetime.utcnow()
    print('{} | {} | {}'.format(now, level.ljust(5), msg), flush=True)

def info(msg):
    log("INFO", msg)

def error(msg):
    log("ERROR", msg)

def handleExit(signum, frame):
    global running
    error('Caught exit signal')
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
            info("Discovered new channel: {} ({})".format(chan.name, chan.channel_num))

# def lookupDeviceName(account, device_gid):
#     if device_gid not in account['deviceIdMap']:
#         populateDevices(account)

#     deviceName = "{}".format(device_gid)
#     if device_gid in account['deviceIdMap']:
#         deviceName = account['deviceIdMap'][device_gid].device_name
#     return deviceName

# def lookupChannelName(account, chan):
#     if chan.device_gid not in account['deviceIdMap']:
#         populateDevices(account)

#     deviceName = lookupDeviceName(account, chan.device_gid)
#     name = "{}-{}".format(deviceName, chan.channel_num)

#     try:
#         num = int(chan.channel_num)
#         if 'devices' in account:
#             for device in account['devices']:
#                 if 'name' in device and device['name'] == deviceName:
#                     if 'channels' in device and len(device['channels']) >= num:
#                         name = device['channels'][num - 1]
#                         break
#     except:
#         if chan.channel_num == '1,2,3':
#             name = deviceName

#     return name

def extractDataPoints(device):
    excludedDetailChannelNumbers = ['Balance', 'TotalUsage']
    minutesInAnHour = 60
    secondsInAMinute = 60
    wattsInAKw = 1000
    usageDataPoints = []
    cols = ['time','device_id','total']

    if detailedEnabled:
        timestamps = [detailedStartTime + datetime.timedelta(seconds=s) for s in range(intervalSecs)]
        deviceId = [device.device_gid for _ in range(intervalSecs)]
        usageDataPoints.append(timestamps)
        usageDataPoints.append(deviceId)
        for chanNum, chan in device.channels.items():
            if chan.name == 'Balance': continue
            if chanNum != '1,2,3':
                cols.append('chan' + chanNum)
            usage, usage_start_time = account['vue'].get_chart_usage(chan, detailedStartTime, stopTime, scale=Scale.SECOND.value, unit=Unit.KWH.value)
            usages = [float(secondsInAMinute * minutesInAnHour * wattsInAKw) * kwhUsage for kwhUsage in usage]
            usageDataPoints.append(usages)

    else:
        usageDataPoints.append(stopTime)
        usageDataPoints.append(device.device_gid)
        for chanNum, chan in device.channels.items():
            if chan.name == 'Balance': continue
            if chanNum != '1,2,3':
                cols.append('chan' + chanNum)
            usageDataPoints.append(float(minutesInAnHour * wattsInAKw) * chan.usage)

    return cols, usageDataPoints

def submitDataPoints(conn, usageDataPoints, cols):
    cursor = conn.cursor()
    chans = ['chan'+ str(x) for x in range(1,17)]
    # cols = ['time','device_id','total',*chans]
    mgr = CopyManager(conn, 'vue', cols)
    data = list(zip(*usageDataPoints)) if type(usageDataPoints[0]) == list else [usageDataPoints]
    mgr.copy(data)
    conn.commit()

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
    detailedIntervalSecs=getConfigValue("detailedIntervalSecs", 3600)
    lagSecs=getConfigValue("lagSecs", 5)
    detailedStartTime = startupTime

    conn = psycopg2.connect(config['timescale']['connection'])

    while running:
        now = datetime.datetime.utcnow()
        stopTime = now - datetime.timedelta(seconds=lagSecs)
        detailedEnabled = (stopTime - detailedStartTime).total_seconds() >= detailedIntervalSecs

        for account in config["accounts"]:
            if 'vue' not in account:
                account['vue'] = PyEmVue()
                account['vue'].login(username=account['email'], password=account['password'])
                info('Login completed')
                populateDevices(account)

            try:
                deviceGids = list(account['deviceIdMap'].keys())
                usages = account['vue'].get_device_list_usage(deviceGids, stopTime, scale=Scale.MINUTE.value, unit=Unit.KWH.value)
                if usages is not None:
                    for gid, device in usages.items():
                        cols, usageDataPoints = extractDataPoints(device)

                    info('Submitting datapoints to database; account="{}"; points={}'.format(account['name'], len(usageDataPoints)))
                    submitDataPoints(conn, usageDataPoints, cols)
            except Exception as e:
                print(e)
                error('Failed to record new usage data: {}'.format(sys.exc_info())) 
                traceback.print_exc()

        if detailedEnabled:
            detailedStartTime = stopTime + datetime.timedelta(seconds=1)

        pauseEvent.wait(intervalSecs)

    info('Finished')
except:
    error('Fatal error: {}'.format(sys.exc_info())) 
    traceback.print_exc()

