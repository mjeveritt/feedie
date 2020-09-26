# -*- coding: utf-8 -*-

import yaml
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class Feedie:
    bot_owner: List[str] = field(default_factory=lambda: deepcopy(['xstill']))
    cmd_prefix: Optional[str] = '@'
    shorten_service: Optional[str] = 'tinyurl.com'
    wrap_url: Optional[str] = None


@dataclass
class Network:
    server: str = 'chat.freenode.net'
    port: int = 6667
    password: str = ''
    bot_nick: str = 'FEED'
    bot_name: str = 'feedie pyBot v1.1'
    pubmsg_log: bool = False
    announce_delay: float = .5
    default_refresh_delay: float = 35.0
    startup_announces: bool = False


@dataclass
class Feed:
    url: str
    color: str
    channel: str
    channel_key: str = ''
    enabled: bool = True
    refresh_delay: Optional[float] = None


class Config:
    def __init__(self, path: str) -> None:
        self.path = path
        self.reload()

    def reload(self) -> None:
        with open(self.path, 'r') as h:
            raw = yaml.safe_load(h)

        self.feedie = Feedie(**raw.get('feedie', {}))
        self.network = Network(**raw.get('network', {}))
        self.feeds: Dict[str, Feed] = {name: Feed(**r)
                                       for name, r
                                       in raw.get('feeds', {}).items()}
