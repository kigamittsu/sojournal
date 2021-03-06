import os
import sys
import picamera
import time
from datetime import datetime
from gps3 import gps3
from urllib import urlencode
import urllib2
from graphqlclient import GraphQLClient
import json
import pyexif
from fractions import Fraction
import math
from pynput import keyboard
import threading
import redis
import urllib2
import socket
import psutil
import led
import serial
from logging import basicConfig, getLogger, DEBUG, handlers

# logging
basicConfig(level=DEBUG)
logger = getLogger(__name__)
handler = handlers.TimedRotatingFileHandler(
    filename='/home/pi/scripts/logs/takePhotoGPS.log',
    when='D'
)
logger.addHandler(handler)

# redis settings
r = redis.Redis(host='localhost', port=6379, db=0)

GRAPHQL_URL = os.environ['URL_PROD']

# default timeout is about 1 min.
# Now, set the timeout 10 seconds.
socket.setdefaulttimeout(10)

today = datetime.now()
midnight = datetime(year=today.year, month=today.month,
                    day=today.day, hour=0, minute=0, second=0)
startFlag = False
finishFlag = False

# store the failed request
failed = []
# store the last lat
lastLatLon = None
# store the last lat lon exif
lastLatLonExif = None
# store the last time
lasttime = None
# gps info
gps_info = {}


def get_interval_config():
    client = GraphQLClient(GRAPHQL_URL)
    result = None
    config = None
    try:
        result = client.execute(
            """query{ReadIntervalConfig(type:{id: 2}){
                id,
                deviceId,
                startMethod,
                startTimeOfDay,
                startCountdown,
                stopMethod,
                stopTimeOfDay,
                stopCountdown,
                interval
            }}"""
        )
    except urllib2.URLError as err:
        if err.reason.strerror == 'nodename nor servname provided, or not known':
            config = json.loads(r.get('config'))
            pass
        elif err.reason.message == 'timed out':
            config = json.loads(r.get('config'))
            pass
        elif err.reason.errno == 51:
            config = json.loads(r.get('config'))
            pass
        else:
            config = json.loads(r.get('config'))
            pass
        print(err.reason)
        logger.error(err.reason)
    if result is not None:
        config = json.loads(result)["data"]["ReadIntervalConfig"]
        if len(config) != 0:
            config = config[0]
            # temporary setting
            config["interval"] = 2
            r.set('config', json.dumps(config))
        else:
            config = r.get('config')
    return config


def datetime_to_epoch(d):
    return int(time.mktime(d.timetuple()))


def epoch_to_datetime(epoch):
    return datetime(*time.localtime(epoch)[:6])


def take_photo(camera, filename, gps_info):
    global lastLatLonExif
    if lastLatLonExif is None:
        camera.exif_tags['GPS.GPSLatitude'] = "35/1,39/1,2905527/100000"
        camera.exif_tags['GPS.GPSLongitude'] = "139/1,43/1,2017163/50000"
        lastLatLonExif = ("35/1,39/1,2905527/100000",
                          "139/1,43/1,2017163/50000")
    else:
        camera.exif_tags['GPS.GPSLatitude'] = lastLatLonExif[0]
        camera.exif_tags['GPS.GPSLongitude'] = lastLatLonExif[1]
    if gps_info != {} and len(gps_info) > 1:
        lat_deg = to_deg(gps_info['lat'], ["S", "N"])
        lng_deg = to_deg(gps_info['lon'], ["W", "E"])

        exiv_lat = change_to_rational(lat_deg[0]) + "," + change_to_rational(
            lat_deg[1]) + "," + change_to_rational(lat_deg[2])
        exiv_lng = change_to_rational(lng_deg[0]) + "," + change_to_rational(
            lng_deg[1]) + "," + change_to_rational(lng_deg[2])
        camera.exif_tags['GPS.GPSLatitude'] = exiv_lat
        # camera.exif_tags['GPS.GPSLatitudeRef'] = str(lat_deg[3])
        # camera.exif_tags['GPS.GPSLongitudeRef'] = str(lng_deg[3])
        camera.exif_tags['GPS.GPSLongitude'] = exiv_lng
        camera.exif_tags['GPS.GPSTimeStamp'] = str(gps_info['time'])
        # camera.exif_tags['GPS.GPSAltitudeRef'] = str(1)
        camera.exif_tags['GPS.GPSAltitude'] = str(gps_info['alt'])
        camera.exif_tags['GPS.GPSTrack'] = str(gps_info['track'])
        print "lattitude is " + exiv_lat
        logger.debug("lattitude is " + exiv_lat)
        print "longitude is " + exiv_lng
        logger.debug("longitude is " + exiv_lng)
        lastLatLonExif = (exiv_lat, exiv_lng)
    # camera warm-up time
    # time.sleep(2)
    camera.capture('/home/pi/scripts/photos/' + filename)
    r.set("lastLatExif", lastLatLonExif[0])
    r.set("lastLonExif", lastLatLonExif[1])


def get_gps(data_stream, gps_socket):
    global lastLatLon
    global gps_info

    counter = 0
    for newdata in gps_socket:
        if counter <= 5:
            counter += 1
            continue
        if newdata is not None:
            data_stream.unpack(newdata)
            if data_stream.TPV['time'] != 'n/a':
                # print 'time : ', data_stream.TPV['time']
                gps_info['time'] = data_stream.TPV['time']
            if (data_stream.TPV['lat'] != 'n/a' or data_stream.TPV['lon'] != 'n/a'):
                # print 'lat : ', data_stream.TPV['lat']
                gps_info['lat'] = data_stream.TPV['lat']
                # print 'lon : ', data_stream.TPV['lon']
                gps_info['lon'] = data_stream.TPV['lon']
                lastLatLon = (gps_info['lat'], gps_info['lon'])
            if data_stream.TPV['alt'] != 'n/a':
                # print 'alt : ', data_stream.TPV['alt']
                gps_info['alt'] = data_stream.TPV['alt']
            if data_stream.TPV['track'] != 'n/a':
                # print 'track : ', data_stream.TPV['track']
                gps_info['track'] = data_stream.TPV['track']
    return gps_info


def to_deg(value, loc):
    """convert decimal coordinates into degrees, munutes and seconds tuple
    Keyword arguments: value is float gps-value, loc is direction list ["S", "N"] or ["W", "E"]
    return: tuple like (25, 13, 48.343 ,'N')
    """
    if value < 0:
        loc_value = loc[0]
    elif value > 0:
        loc_value = loc[1]
    else:
        loc_value = ""
    abs_value = abs(value)
    deg = int(abs_value)
    t1 = (abs_value-deg)*60
    min = int(t1)
    sec = round((t1 - min) * 60, 5)
    return (deg, min, sec, loc_value)


def change_to_rational(number):
    """convert a number to rantional
    Keyword arguments: number
    return: tuple like (1, 2), (numerator, denominator)
    """
    f = Fraction(str(number))
    return str(f.numerator) + "/" + str(f.denominator)


def upload_server(filename, timestr, gps_info, serialNo):
    global lastLatLon
    global failed
    with open('/home/pi/scripts/photos/' + filename, 'rb') as f:
        data = f.read()
        base64 = data.encode('base64')

        client = GraphQLClient(GRAPHQL_URL)
        result = None

        if lastLatLon is None:
            lastLatLon = (35.657995, 139.727666)

        if gps_info != {}:
            if gps_info.viewkeys() >= {'lon', 'lat', 'alt', 'track'}:
                try:
                    query = 'mutation{CamCreatePhoto(input: {imageFile: \"\"\"' + \
                        base64 + '\"\"\", longitude: ' + \
                        str(gps_info['lon']) + ', latitude: ' + \
                        str(gps_info['lat']) + ', deviceSerial: \"' + serialNo + '\", altitude: ' + \
                        str(gps_info['alt']) + ', bearing: ' + \
                        str(gps_info['track']) + '})}'
                    result = client.execute(
                        query
                    )
                except urllib2.URLError as err:
                    print err.reason
                    logger.error(err.reason)
                    if err.reason.strerror == 'nodename nor servname provided, or not known':
                        failed.append(query)
                        pass
                    elif err.reason.message == 'timed out':
                        failed.append(query)
                        pass
                    elif err.reason.errno == 51:
                        failed.append(query)
                        pass
                    else:
                        failed.append(query)
                        pass
                if result is not None:
                    print result
                    logger.info(result)
        else:
            try:
                query = "mutation{CamCreatePhoto(input: {imageFile: \"\"\"" + \
                    base64 + "\"\"\",  latitude: " + \
                    str(lastLatLon[0]) + ", longitude: " + \
                    str(lastLatLon[1]) + \
                    ", deviceSerial: \'" + serialNo + "\'})}"
                result = client.execute(
                    query
                )
            except urllib2.URLError as err:
                print err.reason
                logger.error(err.reason)
                if err.reason.strerror == 'nodename nor servername provided, or not known':
                    failed.append(query)
                    pass
                elif err.reason.message == 'timed out':
                    failed.append(query)
                    pass
                elif err.reason.errno == 51:
                    failed.append(query)
                    pass
                else:
                    failed.append(query)
                    pass
            if result is not None:
                print result
                logger.debug(result)
        r.set("lastLat", lastLatLon[0])
        r.set("lastLon", lastLatLon[1])


def upload_retry(request):
    try:
        client = GraphQLClient(GRAPHQL_URL)
        result = None
        result = client.execute(
            request
        )
    except urllib2.URLError as err:
        logger.error(err.reason)
        print err.reason
        if err.reason.strerror == 'nodename nor servname provided, or not known':
            failed.append(request)
            pass
        elif err.reason.message == 'timed out':
            failed.append(request)
            pass
        elif err.reason.errno == 51:
            failed.append(request)
            pass
        else:
            failed.append(request)
            pass
    if result is not None:
        logger.debug(result)
        print(result)


def take_photo_with_gps(interval_config, serialNo):
    global startFlag
    global lastLatLon
    global lastLatLonExif
    count = 0
    camera = picamera.PiCamera()
    camera.led = False

    if interval_config["startMethod"] == "startTimeOfDay":
        lasttime = interval_config["startTimeOfDay"]
    else:
        lasttime = (datetime.now() - midnight).seconds

    while True:
        led.control("green", "flash")
        currenttime = datetime.now()
        deltatime = (currenttime - midnight).seconds
        if interval_config["startMethod"] == "startTimeOfDay":
            if (deltatime - lasttime) >= interval_config["interval"]:
                count += 1
                lasttime = deltatime
                timestr = datetime.now().strftime('%Y-%m-%dT%H_%M_%S%f')
                filename = timestr + '.jpg'
                print "gps_infomation: " + str(gps_info)
                logger.info("gps_infomation: " + str(gps_info))
                print "starting take photo " + filename
                logger.info("starting take photo " + filename)
                take_photo(camera, filename, gps_info)
                print "finish take photo " + filename
                logger.info("finish take photo " + filename)
                print "starting upload photo " + filename
                logger.info("starting upload photo " + filename)
                upload_server(filename, timestr, gps_info, serialNo)
                # upload_s3(filename)
                print "finish upload photo " + filename
                logger.info("finish upload photo " + filename)
                disk_usage = psutil.disk_usage('/home/')
                print disk_usage
                logger.info(disk_usage)
                if disk_usage.percent >= 95:
                    raise Exception
            if startFlag == False:
                print "uploaded: " + str(count)
                logger.info("uploaded: " + str(count))
                break
        else:
            if (deltatime - lasttime) >= interval_config["interval"]:
                count += 1
                lasttime = deltatime
                timestr = datetime.now().strftime('%Y-%m-%dT%H_%M_%S%f')
                filename = timestr + '.jpg'
                print "gps_infomation: " + str(gps_info)
                logger.info("gps_infomation: " + str(gps_info))
                print "starting take photo " + filename
                logger.info("starting take photo " + filename)
                take_photo(camera, filename, gps_info)
                print "finish take photo " + filename
                logger.info("finish take photo " + filename)
                print "starting upload photo " + filename
                logger.info("starting upload photo " + filename)
                upload_server(filename, timestr, gps_info, serialNo)
                print "finish upload photo " + filename
                logger.info("finish upload photo " + filename)
                disk_usage = psutil.disk_usage('/')
                print disk_usage
                logger.info(disk_usage)
                if disk_usage.percent >= 95:
                    raise Exception
            if startFlag == False:
                break
        # time.sleep(0.5)
    print "failed requests are " + str(len(failed))
    logger.info("failed requests are " + str(len(failed)))
    if len(failed) != 0:
        r.set("requests", json.dumps(failed))
        print "stored failed requests to redis"
        logger.info("stored failed requests to redis")
    # camera.stop_preview()
    camera.close()


def on_press(key):
    print threading.current_thread().name
    global startFlag
    global finishFlag
    if startFlag == False:
        if key == keyboard.Key.tab:
            startFlag = True
            return False
        # stop the loop
        if key == keyboard.Key.down:
            logger.info("script will close")
            print "script will close"
            startFlag = False
            finishFlag = True
            logger.info("please press key again")
            print "please press key again"
            return False
        elif key == keyboard.Key.page_down:
            logger.info("script will close")
            print "script will close"
            startFlag = False
            finishFlag = True
            logger.info("please press key again")
            print "please press key again"
            return False
    else:
        if key == keyboard.Key.tab:
            startFlag = False
            return False
        # stop the loop
        if key == keyboard.Key.down:
            logger.info("script will close")
            print "script will close"
            startFlag = False
            finishFlag = True
            logger.info("please press key again")
            print "please press key again"
            return False
        elif key == keyboard.Key.page_down:
            print "script will close"
            logger.info("script will close")
            startFlag = False
            finishFlag = True
            print "please press key again"
            logger.info("please press key again")
            return False


class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self, *args, **kwargs):
        super(StoppableThread, self).__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()
        # self.join()

    def stop_timeofday(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()


if __name__ == "__main__":
    serialNo = serial.getserial()

    gps_socket = gps3.GPSDSocket()
    data_stream = gps3.DataStream()
    gps_socket.connect()
    gps_socket.watch()

    retries = r.get("requests")
    if retries is not None:
        requests = json.loads(retries)
        r.delete('requests')
        led.control("green", "flash")
        for request in requests:
            upload_retry(request)
        if len(failed) != 0:
            r.set("requests", json.dumps(failed))
        led.control("green", "off")
    lastLat = r.get("lastLat")
    lastLon = r.get("lastLon")
    lastLatExif = r.get("lastLatExif")
    lastLonExif = r.get("lastLonExif")
    if lastLat is not None and lastLon is not None:
        lastLatLon = (lastLat, lastLon)
        lastLatLonExif = (lastLatExif, lastLonExif)

    thread_gps = StoppableThread(
        target=get_gps, args=(data_stream, gps_socket))
    thread_gps.start()

    try:
        while True:
            interval_config = get_interval_config()
            thread_photo = StoppableThread(target=take_photo_with_gps,
                                           args=([interval_config, serialNo]))
            # print threading.current_thread().name
            if interval_config["startMethod"] == "startTimeOfDay":
                led.control("green", "on")
                print "startMethod is startTimeOfDay"
                logger.info("startMethod is startTimeOfDay")
                starttime = interval_config["startTimeOfDay"]
                endtime = interval_config["stopTimeOfDay"]
                currenttime = datetime.now()
                deltatime = (currenttime - midnight).seconds
                # print starttime - deltatime
                if (starttime - deltatime == 0):
                    print "Start Thread"
                    logger.info("Start Thread")
                    startFlag = True
                    thread_photo.start()
                if (interval_config["stopMethod"] == "stopTimeOfDay"):
                    if (deltatime - endtime == 0):
                        startFlag = False
                        print "startflag is " + str(startFlag)
                        logger.debug("startflag is " + str(startFlag))
                        thread_photo.stop_timeofday()
                        print "Stop Thread"
                        logger.info("Stop Thread")
                if (interval_config["stopMethod"] == "stopButton"):
                    with keyboard.Listener(
                            on_press=on_press) as listener:
                        listener.join()
                    print "startflag is " + str(startFlag)
                    logger.debug("startflag is " + str(startFlag))
                    if (startFlag == False):
                        thread_photo.stop()
                        print "stop thread"
                        logger.info("Stop Thread")
                if (interval_config["stopMethod"] == "stopCountDown"):
                    time.sleep(interval_config["stopCountdown"])
                    print startFlag
                    startFlag = False
                    thread_photo.stop()
                    print "stop thread"
                    logger.info("Stop Thread")

            if interval_config["startMethod"] == "startButton":
                led.control("green", "on")
                print "startMethod is startButton"
                logger.info("startMethod is startButton")
                print "waiting input"
                logger.info("waiting input")
                with keyboard.Listener(
                        on_press=on_press) as listener:
                    listener.join()
                print "startflag is " + str(startFlag)
                logger.info("startflag is " + str(startFlag))
                if (startFlag):
                    thread_photo.start()
                    print "start thread"
                    logger.info("start thread")
                if (interval_config["stopMethod"] == "stopTimeOfDay"):
                    if (deltatime - endtime == 0):
                        startFlag = False
                        print "startflag is " + str(startFlag)
                        logger.info("startflag is " + str(startFlag))
                        thread_photo.stop()
                        print "Stop Thread"
                        logger.info("Stop Thread")
                if (interval_config["stopMethod"] == "stopButton"):
                    with keyboard.Listener(
                            on_press=on_press) as listener:
                        listener.join()
                    print "startflag is " + str(startFlag)
                    logger.info("startflag is " + str(startFlag))
                    if (startFlag == False):
                        thread_photo.stop()
                        print "stop thread"
                        logger.info("stop thread")
                if (interval_config["stopMethod"] == "stopCountDown"):
                    time.sleep(interval_config["stopCountdown"])
                    startFlag = False
                    thread_photo.stop()
                    print "stop thread"
                    logger.info("stop thread")
            if interval_config["startMethod"] == "startCountDown":
                led.control("green", "on")
                print "startMethod is startCountDown"
                logger.info("startMethod is startCountDown")
                with keyboard.Listener(
                        on_press=on_press) as listener:
                    listener.join()
                print "startflag is " + str(startFlag)
                logger.info("startflag is " + str(startFlag))
                if (startFlag):
                    print "waiting :" + \
                        str(interval_config["startCountdown"]) + " seconds..."
                    logger.info(
                        "waiting :" + str(interval_config["startCountdown"]) + " seconds...")
                    time.sleep(interval_config["startCountdown"])
                    thread_photo.start()
                    logger.info("start thread")
                    print "start thread"
                if (interval_config["stopMethod"] == "stopTimeOfDay"):
                    if (deltatime - endtime == 0):
                        startFlag = False
                        print "startflag is " + str(startFlag)
                        logger.info("startflag is " + str(startFlag))
                        thread_photo.stop()
                        print "Stop Thread"
                        logger.info("Stop Thread")
                if (interval_config["stopMethod"] == "stopButton"):
                    with keyboard.Listener(
                            on_press=on_press) as listener:
                        listener.join()
                    print "startflag is " + str(startFlag)
                    logger.info("startflag is " + str(startFlag))
                    if (startFlag == False):
                        thread_photo.stop()
                        print "stop thread"
                        logger.info("stop thread")
                if (interval_config["stopMethod"] == "stopCountDown"):
                    time.sleep(interval_config["stopCountdown"])
                    startFlag = False
                    thread_photo.stop()
                    print "stop thread"
                    logger.info("stop thread")
            # time.sleep(0.5)
            if finishFlag == True:
                # print threading.current_thread().name
                led.control("green", "off")
                logger.info("finish scripts")
                sys.exit(0)
    except KeyboardInterrupt:
        led.control("green", "off")
        sys.exit(0)
