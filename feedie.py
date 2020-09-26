#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# IRC Bot to announce RSS FEEDS
#
#   Creator: meigrafd, Vladimír Štill
#   Copyright (C) 2020 by feedie@vstill.eu published under the Creative Commons License (BY-NC-SA)
#   Copyright (C) 2017 by meiraspi@gmail.com published under the Creative Commons License (BY-NC-SA)
#
# Based on:
# https://github.com/mrsmn/wookie
# https://github.com/jaraco/irc
#
# Required:
# apt-get install python3-pip libffi-dev
# pip3 install pyopenssl feedparser irc requests sgmllib3k
#
# TODO:
# http://stackoverflow.com/a/36572948
#
#

from irc.client import SimpleIRCClient  # type: ignore
from jaraco.stream import buffer        # type: ignore
from datetime import timedelta
from queue import Queue
from typing import List
import re
import os
import sys
import irc          # type: ignore
import time
import random
import socket
import signal
import sgmllib      # type: ignore
import requests
import itertools
import threading
import feedparser   # type: ignore
from config import Config, Feed, Network


def _termHandler(signalNumber, stackFrame):
    raise SystemExit('Signal #%s.' % signalNumber)
signal.signal(signal.SIGTERM, _termHandler)


#class IgnoreErrorsBuffer(buffer.DecodingLineBuffer):
#    def handle_exception(self):
#        pass
#irc.client.ServerConnection.buffer_class = IgnoreErrorsBuffer


class PeriodicExecutor(threading.Thread):
    def __init__(self, interval, func, **kwargs):
        """ Execute func(params) every 'interval' seconds """
        threading.Thread.__init__(self, name="PeriodicExecutor")
        self.setDaemon(1)
        self._finished = threading.Event()
        self._interval = interval
        self._func = func
        self._params = kwargs
    
    def setInterval(self, interval):
        """Set the number of seconds we sleep between executing our task"""
        self._interval = interval
    
    def shutdown(self):
        """Stop this thread"""
        self._finished.set()
    
    def run(self):
        while 1:
            if self._finished.isSet(): return
            self._func(**self._params)
            # sleep for interval or until shutdown
            self._finished.wait(self._interval)


class _Feeds(threading.Thread):
    def __init__(self, bot, config: Config):
        threading.Thread.__init__(self)
        self.config = config
        self.setDaemon(1)
        self.bot = bot
        self.services = {
            'v.gd':         'https://v.gd/create.php?format=simple&',
            'tinyurl.com':  'http://tinyurl.com/api-create.php?',
        }
        self.headers = {
            'User-Agent': "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-GB; rv:1.9.2.2) Gecko/20100316 Firefox/3.6.2",
        }
        self.service_url = None
        if config.feedie.shorten_service is not None:
            self.service_url = self.services.get(config.feedie.shorten_service)
        self.lastRequest = dict()
        self.cachedFeeds = dict()
        self.feeds_num = len(self.config.feeds)
        self.init_periodic_feedRefresh()
    
    
    def run(self):
        signal.pause()
    
    
    def shorten_url(self, long_url: str) -> str:
        if self.service_url is None:
            return long_url

        get_params = {'url': long_url}
        try:
            response = requests.get(self.service_url, params=get_params, headers=self.headers)
        except (requests.ConnectionError, requests.HTTPError, requests.Timeout) as error:
            print('%s:%s' % (error.code, error.msg), flush=True)
            return long_url
            #sleep(2)
            #response = self.shorten_url(long_url)
        try: response.close()
        except: pass
        return response.text
    
    
    def getHeadlines(self, feed):
        headlines = []
        for d in feed['items']:
            if 'title' in d:
                title = d.get('title')
                link = d.get('link')
                if link:
                    headlines.append((title, link))
                else:
                    headlines.append((title, None))
        return headlines
    
    
    def getFeed(self, url, name):
        def error(s):
            return {'items': [{'title': s}]}
        try:
            #print(f'Downloading new feed from {url}', flush=True)
            results = feedparser.parse(url)
            if 'bozo_exception' in results:
                raise results['bozo_exception']
        except sgmllib.SGMLParseError:
            return error('Invalid (unparsable) RSS feed.')
        except socket.timeout:
            return error('Timeout downloading feed.')
        except Exception as e:
            # These seem mostly harmless. We'll need reports of a kind that isn't.
            #print('Allowing bozo_exception "%r" through.' % e)
            pass
        if results.get('feed', {}):
            self.cachedFeeds[name] = results
            self.lastRequest[name] = time.time()
        else:
            print('Not caching results; feed is empty.', flush=True)
        try:
            return self.cachedFeeds[name]
        except KeyError:
            # If there's a problem retrieving the feed, we should back off
            # for a little bit before retrying so that there is time for
            # the error to be resolved.
            self.lastRequest[name] = time.time() - .5 * 180
            return error('Unable to download feed.')
    
    
    def init_periodic_feedRefresh(self):
        self.feed_thread = dict()
        feeds_oneTimer = []
        for name, feed in self.config.feeds.items():
            if not feed.enabled:
                continue
            if feed.refresh_delay is not None:
                self.feed_thread[name] = PeriodicExecutor(feed.refresh_delay, self.feed_refresh, feed=feed, name=name).start()
            else:
                feeds_oneTimer.append((name, feed))
        
        if feeds_oneTimer:
            refresh_time = self.config.network.default_refresh_delay
            self.feed_thread["__oneTimer__"] = PeriodicExecutor(refresh_time, self.feed_refresh_oneTimer, feeds=feeds_oneTimer).start()
    
    
    def feed_refresh_oneTimer(self, feeds):
        for name, feed in feeds:
            self.feed_refresh(feed, name)
    
    
    def feed_refresh(self, feed: Feed, name: str):
        url = feed.url
        try:
            oldresults = self.cachedFeeds[name]
            oldheadlines = self.getHeadlines(oldresults)
        except KeyError:
            oldheadlines = []
        
        if not self.config.network.startup_announces and not oldheadlines:
            newresults = self.getFeed(url, name)
            return
        else:
            newresults = self.getFeed(url, name)
            newheadlines = self.getHeadlines(newresults)
        
        if len(newheadlines) == 1:
            s = newheadlines[0][0]
            if s in ('Timeout downloading feed.', 'Unable to download feed.'):
                print('%s %u', s, url, flush=True)
                return
        
        def canonize(headline):
            return (tuple(headline[0].lower().split()), headline[1])
        
        oldheadlines = set(map(canonize, oldheadlines))
        
        for (i, headline) in enumerate(newheadlines):
            entry_old = oldresults['entries'][i]
            entry_new = newresults['entries'][i]
            time_old = entry_old.get('updated_parsed', entry_new.get('published_parsed'))
            time_new = entry_new.get('updated_parsed', entry_new.get('published_parsed'))
            if canonize(headline) in oldheadlines and time_old >= time_new:
                newheadlines[i] = None
        
        if newheadlines:
            for i, headline in enumerate(newheadlines):
                if headline is None:
                    continue
                if headline[1]:
                    title = headline[0]
                    link = self.shorten_url(newresults['entries'][i].get('link', 'no_url'))
                    feedName = self.bot.mircColor(name, feed.color)
                    feedTitle = self.bot.mircColor(title, 'blue')
                    try:
                        chan = feed.channel
                    except KeyError:
                        # send to all channels
                        chan = None
                    fmturl = self.bot.underline(link)
                    wrap_url = self.config.feedie.wrap_url
                    if wrap_url:
                        fmturl = wrap_url[0] + fmturl + wrap_url[-1]
                    self.on_rss_entry(chan=chan, text='{0} {1} {2}'.format(feedName, feedTitle, fmturl))
    
    
    def on_rss_entry(self, chan=None, text=''):
        if chan:
            self.bot.queue_send(text, chan)
        else:
            for chan in {feed.channel for feed in self.config.feeds.values()}:
                self.bot.queue_send(text, chan)



#-------------------------------------------------------------------



class ReconnectStrategy(object):
    min_interval = 60
    max_interval = 300
    def __init__(self, **attrs):
        vars(self).update(attrs)
        assert 0 <= self.min_interval <= self.max_interval
        self._check_scheduled = False
        self.attempt_count = itertools.count(1)
    
    def run(self, bot):
        self.bot = bot
        if self._check_scheduled:
            return
        # calculate interval in seconds based on connection attempts
        intvl = 2**next(self.attempt_count) - 1
        # limit the max interval
        intvl = min(intvl, self.max_interval)
        # add jitter and truncate to integer seconds
        intvl = int(intvl * random.random())
        # limit the min interval
        intvl = max(intvl, self.min_interval)
        threading.Timer(intvl, self.check).start()
        self._check_scheduled = True
    
    def check(self):
        self._check_scheduled = False
        if not self.bot.connection.is_connected():
            self.run(self.bot)
            self.bot.jump_server()


class feedie(SimpleIRCClient):
    def __init__(self, config):
        SimpleIRCClient.__init__(self)
        self.config = config
        self.start_time = time.time()
        self.reconnection_interval = 10
        self.recon = ReconnectStrategy(min_interval=self.reconnection_interval)
        self.channels = dict()
        self.msg_queue = Queue()
        self.msg_queue_thread = threading.Thread(target=self.msq_queue_tasks, args=(self.connection, self.msg_queue, config.network.announce_delay,))
        self.msg_queue_thread.setDaemon(1)
        self.msg_queue_thread.start()
        self.history_manager()
        self.init_mircColors()
    
    
    def msq_queue_tasks(self, connection, queue, delay):
        while True:
            (msg, target) = queue.get()
            try:
                connection.privmsg(target, msg)
            except irc.client.ServerNotConnectedError as error:
                print("Error: %s" % error, flush=True)
                self.jump_server()
            time.sleep(delay)
    
    
    def queue_send(self, msg, target):
        self.msg_queue.put( (msg.strip(), target) )
    
    
    def on_nicknameinuse(self, serv, ev):
        serv.nick(serv.get_nickname() + "_")
    
    
    def on_welcome(self, serv, ev):
        if self.config.network.password:
            serv.privmsg("nickserv", "IDENTIFY {}".format(self.config.network.password))
            serv.privmsg("chanserv", "SET irc_auto_rejoin ON")
            serv.privmsg("chanserv", "SET irc_join_delay 0")
        
        for name, feed in self.config.feeds.items():
            if not feed.enabled:
                continue
            try:
                serv.join(feed.channel, key=feed.channel_key)
            except:
                serv.join(feed.channel)
    
    
    def on_disconnect(self, serv, env):
        self.channels = dict()
        self.recon.run(self)
    
    
    def on_kick(self, serv, ev):
        serv.join(ev.target())
    
    
    def on_invite(self, serv, ev):
        serv.join(ev.arguments[0])
    
    
    def on_ctcp(self, serv, ev):
        nick = ev.source.nick
        if ev.arguments[0] == "PING":
            if len(ev.arguments) > 1:
                serv.ctcp_reply(nick, "PING " + ev.arguments[1])
        elif ev.arguments[0].upper() == 'VERSION':
            serv.ctcp_reply(nick, self.config.network.bot_name)
    
    
    def on_privmsg(self, serv, ev):
        if self.config.cmd_prefix is None:
            return

        nick = ev.source.nick
        message = ev.arguments[0]
        serv.privmsg(nick, "You said: " + message)
        arguments = message.split(' ')
        
        if nick in self.config.feedie.bot_owner:
            if '.say' == arguments[0] and len(arguments) > 2:
                serv.privmsg(arguments[1], message.replace(arguments[0], '').replace(arguments[1], '')[2:])
            elif '.act' == arguments[0] and len(arguments) > 2:
                serv.action(arguments[1], message.replace(arguments[0], '').replace(arguments[1], '')[2:])
            elif '.join' == arguments[0] and len(arguments) > 2:
                serv.join(message[3:])
            elif '.part' == arguments[0] and len(arguments) > 2:
                serv.part(message[3:])
    
    
    def on_pubmsg(self, serv, ev):
        if self.config.cmd_prefix is None:
            return

        nick = ev.source.nick
        chan = ev.target
        message = ev.arguments[0]
        
        if self.config.network.pubmsg_log:
            event_time = time.strftime('[%H:%M:%S]', time.localtime())
            record = '{0} {1}@{2}: {3}'.format(event_time, nick, chan, message)
            with open(self.irc_entries, "a") as f:
                f.write("{}\n".format(record))
            print(record, flush=True)
        
        if nick in self.config.feedie.bot_owner:
            try:
                if self.config.feedie.cmd_prefix + 'rehash' == message.lower():
                    self.config.reload()
                    serv.privmsg(chan, '{0}'.format(self.bold(self.mircColor("Successfully rehashed.", 'blue'))))
                elif self.config.feedie.cmd_prefix + 'restart' == message.lower():
                    #self.restart_bot(serv, ev)
                    print("missing feature: %s" % message, flush=True)
                elif self.config.feedie.cmd_prefix + 'quit' == message.lower():
                    serv.disconnect()
                    sys.exit(1)
            except OSError as error:
                serv.disconnect()
                print(error, flush=True)
                sys.exit(1)

        if self.config.feedie.cmd_prefix + 'help' == message.lower():
            serv.privmsg(
                chan, '{0}{1}{2}{0}{1} {3}help || '
                      '{3}version || {3}uptime || {3}restart || {3}quit || {3}feeds'.format(
                            self.BOLD, self.UNDERLINE, self.mircColor("Available commands:", 'blue'), self.config.feedie.cmd_prefix))

        elif self.config.feedie.cmd_prefix + 'version' == message.lower():
            serv.privmsg(chan, '{0}{1}{2}'.format(self.BOLD, self.mircColor(self.config.network.bot_name, 'blue'), self.END))

        elif self.config.feedie.cmd_prefix + 'uptime' == message.lower():
            uptime_raw = round(time.time() - self.start_time)
            uptime = timedelta(seconds=uptime_raw)
            serv.privmsg(chan, '{0}{1} {2} {3}'.format(self.BOLD, self.mircColor("[UPTIME]", 'teal'), uptime, self.END))

        elif self.config.feedie.cmd_prefix + 'feeds' == message.lower():
            for name, feed in self.config.feeds.items():
                if not feed.enabled:
                    continue
                if not feed.channel == chan:
                    continue
                self.queue_send('{0}: {1}'.format(self.mircColor(name, feed.color), feed.url), chan)
    
    
    def history_manager(self):
        #home = '{}/.feedie_logs'.format(os.environ.get('HOME'))
        home = '/tmp'
        self.feedie_path = os.path.dirname(os.path.realpath(__file__))
        if self.config.network.pubmsg_log:
            if os.path.exists(home) is False:
                os.system('mkdir {}'.format(home))
            self.irc_entries = '{}/irc-entries'.format(home)
            if os.path.exists(self.irc_entries) is False:
                os.system('touch {}'.format(self.irc_entries))
    
    
    def disconnect(self, msg="I'll be back!"):
        self.connection.disconnect(msg)
    
    
    def jump_server(self, msg="Changing servers"):
        """Connect to a new server, possibly disconnecting from the current."""
        if self.connection.is_connected():
            self.connection.disconnect(msg)
        self._connect()
    
    
    def bold(self, s):
        """Returns the string s, bolded."""
        return '\x02%s\x02' % s
    
    
    def reverse(self, s):
        """Returns the string s, reverse-videoed."""
        return '\x16%s\x16' % s
    
    
    def underline(self, s):
        """Returns the string s, underlined."""
        return '\x1F%s\x1F' % s
    
    def stripBold(self, s):
        """Returns the string s, with bold removed."""
        return s.replace('\x02', '')
    
    
    _stripColorRe = re.compile(r'\x03(?:\d{1,2},\d{1,2}|\d{1,2}|,\d{1,2}|)')
    def stripColor(self, s):
        """Returns the string s, with color removed."""
        return feedie._stripColorRe.sub('', s)
    
    
    def stripReverse(self, s):
        """Returns the string s, with reverse-video removed."""
        return s.replace('\x16', '')
    
    
    def stripUnderline(self, s):
        """Returns the string s, with underlining removed."""
        return s.replace('\x1f', '').replace('\x1F', '')
    
    
    def stripFormatting(self, s):
        """Returns the string s, with all formatting removed."""
        # stripColor has to go first because of some strings, check the tests.
        s = self.stripColor(s)
        s = self.stripBold(s)
        s = self.stripReverse(s)
        s = self.stripUnderline(s)
        return s.replace('\x0f', '').replace('\x0F', '')
    
    
    def init_mircColors(self):
        self.mircColors = dict({
            'white': '0',
            'black': '1',
            'blue': '2',
            'green': '3',
            'red': '4',
            'brown': '5',
            'purple': '6',
            'orange': '7',
            'yellow': '8',
            'light green': '9',
            'teal': '10',
            'light blue': '11',
            'dark blue': '12',
            'pink': '13',
            'dark grey': '14',
            'light grey': '15',
            'dark gray': '14',
            'light gray': '15',
        })
        self.BOLD = '\x02'
        self.ITALIC = '\x1D'
        self.UNDERLINE = '\x1F'
        self.SWAP = '\x16'
        self.END = '\x0F'
    
    
    def mircColor(self, s, fg=None, bg=None):
        """Returns s with the appropriate mIRC color codes applied."""
        if fg is None and bg is None:
            return s
        elif bg is None:
            fg = self.mircColors[str(fg)]
            return '\x03%s%s\x03' % (fg.zfill(2), s)
        elif fg is None:
            bg = self.mircColors[str(bg)]
            # According to the mirc color doc, a fg color MUST be specified if a
            # background color is specified.  So, we'll specify 00 (white) if the
            # user doesn't specify one.
            return '\x0300,%s%s\x03' % (bg.zfill(2), s)
        else:
            fg = self.mircColors[str(fg)]
            bg = self.mircColors[str(bg)]
            # No need to zfill fg because the comma delimits.
            return '\x03%s,%s%s\x03' % (fg, bg.zfill(2), s)


def main(argv: List[str]):
    try:
        config = Config(argv[1])
        bot = feedie(config)
        bot.buffer_class = buffer.LenientDecodingLineBuffer
        irc.client.ServerConnection.buffer_class = buffer.LenientDecodingLineBuffer
        irc.client.ServerConnection.buffer_class.errors = 'replace'
        bot.connect(config.network.server, config.network.port, config.network.bot_nick, config.network.bot_name)
        _Feeds(bot, config).start()
        bot.start()
    
    except (KeyboardInterrupt, SystemExit):
        sys.exit(1)
    except OSError as error:
        print(error, flush=True)
        sys.exit(1)
    except irc.client.ServerConnectionError as error:
        print(error, flush=True)
        sys.exit(1)
    except UnicodeDecodeError:
        pass


if __name__ == "__main__":
    main(sys.argv)


#EOF
