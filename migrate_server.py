#!/usr/bin/env python3
#
# Migrate all rooms from an old to a new matrix server:
# 1. Create all rooms on new server which exist on the old server instance
# 2. Copy all contents from rooms on old server to newly created roooms
# 

# For forward references
from __future__ import annotations
from dataclasses import dataclass

import toml
import argparse
import logging
import pathlib
import sys
import nio
import asyncio
import io

HOME = pathlib.Path.home()

def sys_exit(msg, exit=True) -> None:
    print(msg, file=sys.stderr)
    logging.error(msg)
    if exit:
        sys.exit(-1)

@dataclass(kw_only=True)
class Server:
    server:  str = ''
    user: str = ''
    password: str = ''
    token: str = ''
    
class Config:        
    SERVER_CREDS = '.server_creds.toml'
    # Get server config TOML and return as map

    def __init__(self, creds: str = None) -> None:
        self.old_list = ['1', 't', 'p', 'v']
        self.new_list = ['2', 'u', 'q', 'w']
        if creds == None:
            self.creds = HOME / pathlib.Path(self.SERVER_CREDS)
        else:
            self.creds = pathlib.Path(creds)
            
        ns_map = self.parse_cmdline()
        self.read_creds()
        self.get_cmdline(ns_map)
        
    def get_verbose(self) -> bool:
        return self.verbose
    
    def parse_cmdline(self) -> dict[str,str]:
        parser = argparse.ArgumentParser('Process server configurations')
        tokens = ['server', 'user', 'password', 'token']
        self.old_map = {}
        enum0 = list(enumerate(tokens))
        help_list = []
        # Consume copy only in first loop
        enum1 = enum0.copy()
        for old in self.old_list:
            tup = enum1.pop(0)
            self.old_map[old] = tup[1]
            help_list.append('old ' + tup[1])
            
        self.new_map = {}
        for new in self.new_list:
            tup = enum0.pop(0)
            self.new_map[new] = tup[1]
            help_list.append('new ' + tup[1])

        for opt in self.old_list+self.new_list:
            help = help_list.pop(0)
            parser.add_argument('-' + opt, type=str, help=help)
            
        parser.add_argument('-V', '--verbose', action='store_true')
        parser.add_argument('-c', '--config', type=str)
            
        # Construct map from parsed option namespace (skip empty elements)
        ns_map = dict(filter(lambda item: item[1] is not None, vars(parser.parse_args()).items()))
        
        if 'config' in ns_map:
            self.creds = ns_map['config']
        
        self.verbose = ns_map['verbose']
        
        return ns_map
            
    def read_creds(self) -> None:
        with open(self.creds) as f:
            config = toml.load(f)
            
        self.old = Server(**config['old'])
        self.new = Server(**config['new'])

    # Parse cmdline parms overwriting existing config map from TOML file
    def get_cmdline(self, ns_map: dict[str, str]) -> None:
         for key in ns_map:
            if key in self.old_list:
                setattr(self.old, self.old_map[key], ns_map[key])
            else:
                if key in self.new_list:
                    setattr(self.new, self.new_map[key], ns_map[key])
        
    def get_verb(self) -> bool:
        return self.verbose
                 
# HACK:
# Get media from old server before uploading to new server
async def download_mxc(server: Matrix_Server, url: str) -> bytearray:
    response = await server.client.download(mxc=url)
    if hasattr(response, 'body'):
        return response
    else:
        return b''                 
                    
class Matrix_Server:
    device_cnt: int = 1
    def __init__(self, server:Server, verbose=False, old: Matrix_Server = None) -> None:
        self.server = server
        self.verb = verbose
        self.old = old
        self.device = f'migrate_server_{self.device_cnt}'
        self.device_cnt += 1
        # For the display names of created rooms
        self.room_names = []
    
    async def login(self) -> nio.AsyncClient:
        self.client = nio.AsyncClient(
            homeserver=self.server.server,
            user=self.server.user,
            config=nio.AsyncClientConfig(store=nio.store.database.SqliteMemoryStore),
        )
        self.client.device_id = self.device
        # We prefer passwords over access tokens
        if len(self.server.password) > 0:
            login_resp = await self.client.login(password=self.server.password)
        else:
            login_resp = await self.client.login(token=self.server.token)
            
        if self.verb:
            sys_exit(f'Logged into server {self.server.server}', False)
        
        if isinstance(login_resp, (nio.LoginResponse)):
            await self.sync()
            self.client.load_store()
            
            self.rooms = self.client.rooms
            for room in self.rooms:
                self.room_names.append(self.rooms[room].display_name)
        
        else:
            logging.error(f'{str(login_resp)}')
            sys_exit(f'Cannot log into {self.server.server}, aborting')
            
    async def logout(self) -> None:
        resp = await self.client.logout(False)
        if isinstance(resp, nio.LogoutError):
            logging.error(f'Logging out from {self.server.server} failed with {str(resp)}')
            
        if self.verb:
            sys_exit(f'Logged out of server {self.server.server}', False)
        
    async def sync(self) -> None:
        sync_resp = await self.client.sync(full_state=True, timeout=30000,
            # Limit fetch of room events as they will be fetched later
            sync_filter={"room": {"timeline": {"limit": 1}}})
        if isinstance(sync_resp, (nio.SyncError)):
            logging(f'Error syncing: {str(sync_resp)}', False)
        
    def get_rooms(self) -> list[nio.MatrixRoom]:
        return self.rooms

    async def create_room(self, room_name: str) -> nio.MatrixRoom:
        old_room = self.old.get_room(room_name)
        resp = await self.client.room_create(visibility=nio.RoomVisibility.public,
                                             room_version=old_room.room_version,
                                             name=room_name, topic=old_room.topic)
        if isinstance(resp, (nio.RoomCreateError)):
            logging.error(f'Room creation error {str(resp)}')
            sys_exit(f'Cannot create room {room_name}, aborting')
        else:
            if self.verb:
                sys_exit(f'Created room {room_name}', False)
            
        await self.sync()
        # Update cached data
        self.rooms = self.client.rooms
        self.room_names.append(room_name)
        
        return self.get_room(room_name)
        
    async def fetch_room_events(self, start_token: str, room: nio.MatrixRoom, direction: nio.MessageDirection) -> list[nio.Event]:
        events = []
        while True:
            resp = await self.client.room_messages(room.room_id, start_token, 
                                                       limit=1000, direction=direction)
            if isinstance(resp, nio.RoomMessagesError):
                logging.error(f'Failed to get messages for room {room.display_name} @ start_token {start_token}')
                
            if len(resp.chunk) == 0:
                break
            
            events.extend(event for event in resp.chunk if isinstance(event, (nio.RoomMessageFormatted, nio.RedactedEvent, 
                                                                            nio.RoomMessageMedia, nio.RoomEncryptedMedia)))
            start_token = resp.end
            
        if self.verb:
            sys_exit(f'Fetched {len(events)} from room {room.display_name} in direction {str(direction)}', False)
            
        return events

    async def get_room_events(self, room: nio.MatrixRoom) -> list[nio.Event]:
        sync_resp = await self.client.sync(full_state=True, sync_filter={"room": {"timeline": {"limit": 1}}})
        start_token = sync_resp.rooms.join[room.room_id].timeline.prev_batch
        events = await self.fetch_room_events(start_token, room, nio.MessageDirection.back)
        events.reverse()
        events += await self.fetch_room_events(start_token, room, nio.MessageDirection.front)
        if self.verb:
            sys_exit(f'Fetched {len(events)} in total from room {room.display_name}', False)
            
        return events
    
    def get_room(self, display_name: str) -> nio.MatrixRoom:
        if display_name in self.room_names:
            for room in self.rooms:
                if self.rooms[room].display_name == display_name:
                    return self.rooms[room]
        else:
            return None
        
    def get_room_from_id(self, room_id: str) -> nio.MatrixRoom:
        if room_id in self.rooms:
            return self.rooms[room_id]
        
        return None

    async def post_event(self, room: nio.MatrixRoom, event: nio.Event) -> None:
        if isinstance(event, (nio.RoomMessageMedia, nio.RoomEncryptedMedia)):
            # mime = event.mimetype
            media_data_resp = await download_mxc(self.old, event.url)
            body = media_data_resp.body
            name = media_data_resp.filename
            mime = media_data_resp.content_type
            body_size = len(body)
            resp, _ = await self.client.upload(data_provider=io.BytesIO(body), content_type=mime, 
                                               filename=name, filesize=body_size)
            
            if self.verb:
                sys_exit(f'Uploaded {name}, obtained URL {resp.content_uri}', False)
                
            content = {
                'body': name,
                'info': {
                    'size': body_size,
                    'mimetype': mime,
                },
                'url': resp.content_uri
            }
        else:
            content = {
                'body': event.body,
            }
        
        msgtype = event.source['content']['msgtype']
        content['msgtype'] = msgtype
        try:
            await self.client.room_send(room.room_id, message_type='m.room.message', content=content)
        except Exception as e:
            sys_exit(f'Exception {str(e)} occurred during message sending')
            
        if self.verb:
            strexc = str(content['body'])[:20] + '...'
            sys_exit(f'Posted {msgtype} with body {strexc} to room {room.display_name}', False)
            
    async def send_events(self, room: nio.MatrixRoom, events: list[nio.Event]):
        for event in events:
            # Filter messages
            if isinstance(event, (nio.RoomMessageText,nio.RoomMessageMedia, nio.RoomEncryptedMedia)):
                await self.post_event(room, event)
                
        if self.verb:
            sys_exit(f'Posted {len(events)} to room {room.display_name}', False)
            
    def get_room_name(self, room: nio.MatrixRoom) -> str:
        return room.display_name
    
    def get_room_names(self) -> list[str]:
        return self.room_names
    
async def main() -> None:
    LOG_DIR = pathlib.Path(HOME, 'log')
    logging.basicConfig(filename=str(LOG_DIR/pathlib.Path(__file__).stem)+'.log', filemode='a', level=logging.DEBUG, format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s')

    config = Config()
    verb = config.get_verbose()
    
    old = Matrix_Server(config.old, verbose=verb)
    await old.login()
    
    new = Matrix_Server(config.new, verbose=verb, old=old)
    await new.login()
    
    for room in old.rooms:
        room_obj = old.get_room_from_id(room)
        room_name = old.get_room_name(room_obj)
        events = await old.get_room_events(room_obj)
        if len(events) > 0:
            if room_name not in new.get_room_names():
                new_room = await new.create_room(room_name)
            else:
                new_room = new.get_room(room_name)
            
            # Copy events from old to new room
            await new.send_events(new_room, events)
            
    new.logout()
    old.logout()

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
