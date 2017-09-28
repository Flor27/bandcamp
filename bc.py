#!/usr/bin/env python2
import sys
import os.path
import argparse
import json
import shutil
import random
import re
import ConfigParser
import demjson
import time
import glob, os

from pprint import pprint

from Queue import Queue

from urllib2 import build_opener, HTTPCookieProcessor, Request, HTTPHandler
from urllib import urlencode, quote
from cookielib import FileCookieJar, CookieJar, LWPCookieJar
from tempfile import mkstemp
from contextlib import closing
from subprocess import Popen, PIPE

from lxml import html

COOKIES_FILE = '/usr/local/etc/bandcamp.cookies'

URL = 'https://bandcamp.com'
CDN_COVERS = 'https://f4.bcbits.com/img'

cj = LWPCookieJar()

if os.path.isfile(COOKIES_FILE):
    cj.load(COOKIES_FILE)

handler = HTTPHandler(debuglevel=0)
opener = build_opener(handler, HTTPCookieProcessor(cj))
opener.addheaders = [
        ('User-agent', 'Enter you own user agent !'),
        ('Accept', '*/*'),
        ('Accept-Encoding','deflate')
]

TMP_PATH = ''
TMP_FILE_PREFIX = 'tmpS_'
queue = Queue()

# Do we have to download then add cover to downloaded music file ?
ADD_COVER = 1

# Keep the cover file ?
KEEP_COVER_FILE = 0

# Infinite DL ?
INFINITE_DL = 1

# Counts
dledFiles = 0
AlldledFiles = 0
NB_STAGES = 10
lastStage = NB_STAGES

def authenticate(login, pwd):
    data = {
        'callback': '',
        'email': login,
        'password': pwd
    }

    req = Request('{}/listener/dj-login/2014/'.format(URL), urlencode(data))
    resp = opener.open(req)
    cj.save(COOKIES_FILE)

def fetch_history():
    resp = opener.open('{}/history/'.format(URL))
    content = resp.read()
    root = html.fromstring(content)
    return {r.attrib['title'].replace('Listen to ',''): r.attrib['data-id']
        for r in root.xpath('//a[@data-id and @title]')}

def fetch_channels(genre):
    resp = opener.open('{}/search/{}/'.format(URL,  quote(genre)))
    content = resp.read()
    root = html.fromstring(content)
    return {r.attrib['title'].replace('Listen to ',''): r.attrib['data-id']
        for r in root.xpath('//a[@data-id and @title]')}

def fetch_wishlist(user):
    # print '{}/{}/wishlist'.format(URL,  quote(user))
    resp = opener.open('{}/{}/wishlist'.format(URL,  quote(user)))
    content = resp.read()
    root = html.fromstring(content)
    return { a.attrib['href']
        for a in root.xpath('//li[contains(@class,"collection-item-container")]/*//a[contains(@class, "item-link") and not (contains(@class, "also-link"))]') }

def fetch_album(album_url, dlPath):
    try:
        resp = opener.open(album_url)
    except:
        print 'Problem while fetching '+album_url
        return 0

    content = resp.read()

    regex = r'var EmbedData = (\{(.*?)\});'
    jsValues = re.search(regex, content, flags=re.M|re.S)
    jsString = jsValues.group(1).replace('\\r',' ').replace('\\n',' ').decode('utf8').encode('ascii', errors='ignore')
    jsString =  jsString.replace("\\\"","'")
    jsString =  re.sub(r'//.[^,]*$','',jsString,0, flags=re.M)
    jsString = jsString.replace('\n\n','').replace('\n',' ').replace('" + "','')

    try:
        albumNfo = demjson.decode(jsString)
    except:
        print("Fuck 124 !!\n\n")
        return 0


    regex = r'var TralbumData = (\{(.*?)\});'
    jsValues = re.search(regex, content, flags=re.M|re.S)

    jsString = jsValues.group(1).replace('\\r',' ').replace('\\n',' ').decode('utf8').encode('ascii', errors='ignore')
    jsString =  jsString.replace("\\\"","'")
    jsString =  re.sub(r'//.[^,]*$','',jsString,0, flags=re.M)
    jsString = jsString.replace('\n\n','').replace('\n',' ').replace('" + "','')

    try:
        albumDatas = demjson.decode(jsString)
    except:
        print("Fuck 146 !!\n\n")
        pprint(jsString)
        return 0

    albumNfo.update(albumDatas)
    albumNfo['album_art_id'] = albumNfo['art_id']

    if 'album_title' in albumNfo:
        albumTitle = albumNfo['album_title']
    else:
        albumTitle = '_alone_track'

    try:
        dname = os.path.dirname(dlPath + sanitizeFname(albumNfo['artist']) + '/' +sanitizeFname(albumTitle)+'/')
    except:
        print('Fuck 163 !!!\n\n')
        pprint(albumNfo)
        return 0


    if not os.path.exists(dname):
        try:
            os.makedirs(dname)
        except OSError:
            pass

    if 'trackinfo' in albumDatas:
        download_album_cover(albumNfo, dname)

        for song in albumDatas['trackinfo']:
            download_song(song, albumNfo, dname)
    else:
        return 0

def set_mp3_tags(fname, song, albumNfo):
    # mp3info [-i] [-t title] [-a artist] [-l album] [-y year] [-c comment] [-n track] [-g genre] file...
    opts = []

    if 'track_num' in song:
        opts.extend(('-n', song['track_num']))

    if 'title' in song:
        opts.extend(('-t', song['title']))

    #'19 Oct 2016 00:00:00 GMT'
    if 'album_release_date' in albumNfo and albumNfo['album_release_date'] is not None:
        realeaseDate = time.strptime(albumNfo['album_release_date'],"%d %b %Y %H:%M:%S %Z")
        opts.extend(('-y', time.strftime("%Y",realeaseDate)))

    if 'artist' in albumNfo:
        opts.extend(('-a', albumNfo['artist']))

    if 'album_title' in albumNfo:
        opts.extend(('-l', albumNfo['album_title']))

#    opts.extend(('-g', 'Bandcamp'))
    opts = [str(r).encode('utf-8') for r in opts]
    Popen(['mp3info'] + opts + [fname]).wait()

def set_mp3_cover(fname, fcname):
    opts = ['--add', fcname, fname]

    Popen(['mp4art'] + opts).wait()

def download_album_cover(albumNfo, fname):

    fcname = fname + '/cover.jpg'
    if not (os.path.exists(fcname)):
        coverUrl = CDN_COVERS.format(id=albumNfo['album_art_id'])

        try:
            resp = opener.open(coverUrl)
        except:
            return

        fd, tmpfcname = mkstemp('',TMP_FILE_PREFIX,TMP_PATH)
        with closing(os.fdopen(fd, 'w')) as tmpfile:
            shutil.copyfileobj(resp, tmpfile)

        try:
            shutil.move(tmpfcname, fcname)
        except OSError:
            # print '188: unable to move '+tmpfcname
            os.remove(tmpfcname)
            pass

def download_cover(song, album, fname, dlPath):
    if 'album' not in song:
        return

    if 'cdcover' not in song['album']:
        return

    fcname = dlPath + os.path.basename(song['album']['cdcover'])
    if KEEP_COVER_FILE != 1 or not (os.path.exists(fcname)):

        coverUrl = CDN_COVERS + song['album']['cdcover']
        try:
            resp = opener.open(coverUrl)
        except:
            return

        fd, tmpfcname = mkstemp('','tmpC_',TMP_PATH)
        with closing(os.fdopen(fd, 'w')) as tmpfile:
            shutil.copyfileobj(resp, tmpfile)

        set_mp3_cover(fname, tmpfcname)

        #shutil.move(tmpfcname, fcname)
        if KEEP_COVER_FILE == 1:
            try:
                shutil.move(tmpfcname, fcname)
            except OSError:
                # print '218: unable to move '+tmpfcname
                os.remove(tmpfcname)
                pass
            #os.rename(tmpfcname, fcname)
        else:
            os.remove(tmpfcname)

    else:
        set_mp3_cover(fname, fcname)

        if KEEP_COVER_FILE != 1:
            os.remove(fcname)
def sanitizeFname(fname):
    # sys.exit()
    san = re.sub(r'[^a-zA-Z0-9_-]','_',fname.replace(' ','_'))
    return san

def download_song(song, albumNfo, dlPath):
    global dledFiles
    #pprint(sanitizeFname(albumNfo['album_title']))

    if (not 'track_num' in song) or (song['track_num'] is None):
        fname = dlPath + '/' + sanitizeFname(song['title'])+'.mp3'
    else:
        fname = dlPath + '/' + ('%02d' % song['track_num']) + '_' + sanitizeFname(song['title'])+'.mp3'
    #pprint(fname)

    if os.path.exists(fname):
        print 'Already DLed : '+fname
        return 0

    if (not 'file' in song) or (song['file'] is None) or (not 'mp3-128' in song['file']):
        print 'Not available for DL : '+fname
        return 0

    if (not 'http' in song['file']['mp3-128']):
        url = 'https:'+song['file']['mp3-128']
    else:
        url = song['file']['mp3-128']

    print '! Now Dlding : ' + fname

    try:
        resp = opener.open(url)
    except:
        print "Gni !! 322"
        pprint(url)
        return 0

    fd, tmpfname = mkstemp('',TMP_FILE_PREFIX,TMP_PATH)

    with closing(os.fdopen(fd, 'w')) as tmpfile:
        shutil.copyfileobj(resp, tmpfile)

    if ADD_COVER == 1:
        download_cover(song, albumNfo, tmpfname, dlPath)

    print "Setting tags..."
    set_mp3_tags(tmpfname, song, albumNfo)

    try:
        shutil.move(tmpfname, fname)
        # tmpfname.close()
        dledFiles = dledFiles + 1
        print "Done !!"
    except OSError:
        # print '316: unable to move '+tmpfname
        os.remove(tmpfname)
        pass
    return 1

def download_channel(channel, genre, user, dlPath):
    global dledFiles, lastStage, AlldledFiles
    if genre == 'wishlist':
        albums = fetch_wishlist(user)
    elif genre == 'activity':
        albums = fetch_activity(user)
    elif genre == 'artist':
        albums = fetch_artist(user)

    for album in albums :
        print 'Feching album ' + album
        fetch_album(album, dlPath)

    return

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='fetch music from bandcamp wishlist')

    parser.add_argument('configFile', help='path and nale of Config File')

    args = parser.parse_args()

    cfg = ConfigParser.ConfigParser()
    cfg.read(args.configFile)

    TMP_PATH = cfg.get('Downloads','TMP_PATH')

    if not TMP_PATH.endswith('/'):
        TMP_PATH = TMP_PATH+'/'

    if not os.path.exists(TMP_PATH):
        print "Not a dir : "+TMP_PATH+" !"
        sys.exit()

    THREAD_AMOUNT = cfg.getint('Downloads','THREAD_AMOUNT')
    ADD_COVER = cfg.getint('Downloads','ADD_COVER')
    KEEP_COVER_FILE = cfg.getint('Downloads','KEEP_COVER_FILE')
    INFINITE_DL = cfg.getint('Downloads','INFINITE_DL')
    NB_STAGES = cfg.getint('Downloads','NB_STAGES')
    CDN_COVERS = cfg.get('Downloads','CDN_COVERS')
    lastStage = NB_STAGES

    listChannels = [{"SECTION":channel,"NAME":re.sub(r'^CHANNEL:\s?(.*)$',r'\1',channel)} for channel in cfg.sections() if re.match(r"^CHANNEL:",channel) is not None]

    for channel in listChannels:
        print '########################################'
        print 'Found "{}" channel ....\n'.format(channel['NAME'])
        print "Name = " + channel['NAME']
        print "Section = " + channel['SECTION']
        print "Path = " + cfg.get(channel['SECTION'],'PATH')
        print "User = " + cfg.get(channel['SECTION'],'USER')
        print "Genre = " + cfg.get(channel['SECTION'],'GENRE')

        dlPath = cfg.get(channel['SECTION'],'PATH')
        if not dlPath.endswith('/'):
            dlPath = dlPath + '/'

        lastStage = NB_STAGES
        dledFiles = 0

        download_channel(channel['NAME'], cfg.get(channel['SECTION'],'GENRE'), cfg.get(channel['SECTION'],'USER'), dlPath)
    print '########################################'

    for f in glob.glob(TMP_PATH + TMP_FILE_PREFIX + "*"):
        print("Need to remove "+f)
        os.remove(f)
    sys.exit()


