import sys
import json
import time
import random
import asyncio
import logging
import traceback
import configparser
from pathlib import Path
from sqlite3 import OperationalError
from typing import List, Dict, Tuple

import uvloop
from pyrogram.errors import ReactionInvalid
from pyrogram.handlers import MessageHandler
from pyrogram import Client, idle, filters, types
from pyrogram.errors.exceptions.unauthorized_401 import UserDeactivatedBan

from config import CHANNELS, POSSIBLE_KEY_NAMES, EMOJIS
from convertor import SessionConvertor


TRY_AGAIN_SLEEP = 20

BASE_DIR = Path(sys.argv[0]).parent
WORK_DIR = BASE_DIR.joinpath('sessions')
BANNED_SESSIONS_DIR = WORK_DIR.joinpath('banned_sessions')
UNNECESSARY_SESSIONS_DIR = WORK_DIR.joinpath('unnecessary_sessions')

CONFIG_FILE_SUFFIXES = ('.ini', '.json')

logging.basicConfig(filename='logs.log', level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
logging.info('Start reaction bot.')


async def send_reaction(client: Client, message: types.Message) -> None:
    """Handler for sending reactions"""
    emoji = random.choice(EMOJIS)
    try:
        await client.send_reaction(chat_id=message.chat.id, message_id=message.id, emoji=emoji)

    except ReactionInvalid:
        logging.warning(f'{emoji} - INVALID REACTION')
    except UserDeactivatedBan:
        logging.warning('Session banned')
    except Exception:
        logging.warning(traceback.format_exc())


async def make_work_dir() -> None:
    """Create the sessions directory if it does not exist"""
    WORK_DIR.mkdir(exist_ok=True)
    UNNECESSARY_SESSIONS_DIR.mkdir(exist_ok=True)
    BANNED_SESSIONS_DIR.mkdir(exist_ok=True)


async def get_config_files_path() -> List[Path]:
    """Take all the configuration files"""
    return [file for file in WORK_DIR.iterdir() if file.suffix.lower() in CONFIG_FILE_SUFFIXES]


async def config_from_ini_file(file_path: Path) -> Dict:
    """Pull the config from the *.ini file"""
    config_parser = configparser.ConfigParser()
    config_parser.read(file_path)
    section = config_parser.sections()[0]
    return {**config_parser[section]}


async def config_from_json_file(file_path: Path) -> Dict:
    """Pull the config from the *.json file"""
    with open(file_path) as f:
        return json.load(f)


async def get_config(file_path: Path) -> Dict:
    """Return the config file to the path"""
    config_suffixes = {
        '.ini': config_from_ini_file,
        '.json': config_from_json_file,
    }
    suffix = file_path.suffix.lower()
    config = await config_suffixes[suffix](file_path)
    normalized_confing = {'name': file_path.stem}
    for key, values in POSSIBLE_KEY_NAMES.items():
        for value in values:
            if not config.get(value):
                continue
            normalized_confing[key] = config[value]
            break
    return normalized_confing


async def create_apps(config_files_paths: List[Path]) -> List[Tuple[Client, Dict, Path]]:
    """
    Create 'Client' instances from config files.
    **If there is no name key in the config file, then the config file has the same name as the session!**
    """
    apps = []
    for config_file_path in config_files_paths:
        try:
            config_dict = await get_config(config_file_path)
            session_file_path = WORK_DIR.joinpath(config_file_path.with_suffix('.session'))
            apps.append((Client(workdir=WORK_DIR.__str__(), **config_dict), config_dict, session_file_path))
        except Exception:
            logging.warning(traceback.format_exc())
    return apps


async def try_convert(session_path: Path, config: Dict):
    """Try to convert the session if the session failed to start in Pyrogram"""
    convertor = SessionConvertor(session_path, config, WORK_DIR)
    try:
        await convertor.convert()
    except OperationalError:
        await convertor.move_file_to_unnecessary(session_path)
        for suffix in CONFIG_FILE_SUFFIXES:
            config_file_path = session_path.with_suffix(suffix)
            await convertor.move_file_to_unnecessary(config_file_path)
        logging.warning('Preservation of the session failed ' + session_path.stem)


async def move_session_to_ban_dir(session_path: Path):
    """Move file to ban dir"""
    if session_path.exists():
        session_path.rename(BANNED_SESSIONS_DIR.joinpath(session_path.name))

    for suffix in CONFIG_FILE_SUFFIXES:
        config_file_path = session_path.with_suffix(suffix)
        if not session_path.exists():
            continue
        config_file_path.rename(BANNED_SESSIONS_DIR.joinpath(config_file_path.name))


async def main():
    """
    Main function:
        - Create a directory of sessions if not created.
        - Take all config files (*.json, *.ini)
        - Create clients by their config files.
        - Run through clients, add handler, start and join chat
        - Wait for completion and finish (infinitely)
    """

    await make_work_dir()
    config_files = await get_config_files_path()

    apps = await create_apps(config_files)
    if not apps:
        raise Exception('No apps!')

    for app, config_dict, session_file_path in apps:
        message_handler = MessageHandler(send_reaction, filters=filters.chat(CHANNELS))
        app.add_handler(message_handler)

        try:
            await app.start()
        except OperationalError:
            await try_convert(session_file_path, config_dict)
            continue
        except UserDeactivatedBan:
            await move_session_to_ban_dir(session_file_path)
            logging.warning('Session banned - ' + app.name)
            continue
        except Exception:
            logging.warning(traceback.format_exc())
            continue

        for channel in CHANNELS:
            await app.join_chat(channel)

    await idle()

    for app, _, _ in apps:
        await app.stop()


def start():
    """Let's start"""
    uvloop.install()
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except Exception:
        logging.critical(traceback.format_exc())
        logging.info(f'Waiting {TRY_AGAIN_SLEEP} sec. before restarting the program...')
        time.sleep(TRY_AGAIN_SLEEP)


if __name__ == '__main__':
    while True:
        start()
