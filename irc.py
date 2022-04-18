from datetime import datetime
import errno
import json
import os
import re
import socket
from time import sleep
import requests
from beatmaps import filter_map_by_ratings

logger = None
team_mode = {0: "HeadToHead", 1: "TagCoop", 2: "TeamVs", 3: "TagTeamVs"}
score_mode = {0: "Score", 1: "Accuracy", 2: "Combo", 3: "ScoreV2"}
play_mode = {0: "osu!", 1: "Taiko", 2: "Catch the Beat", 3: "osu!Mania"}
bot_mode = {0: "AutoHost", 1: "AutoPick"}
valid_roles = [
    "Host",
    "TeamBlue",
    "TeamRed",
    "Hidden",
    "HardRock",
    "SuddenDeath",
    "Flashlight",
    "SpunOut",
    "NoFail",
    "Easy",
    "Relax",
    "Relax2",
]


class OsuIrc:
    def __init__(
        self, username: str, password: str, rooms=[], host="irc.ppy.sh", port=6667
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.socket = None
        self.stop = False
        self.rooms = rooms
        self.init_rooms()

    def init_rooms(self):
        for room in self.rooms:
            room["name"] = room.get("name").strip()
            room["connected"] = room["created"] = room["configured"] = False
            room["total_users"] = 00
            room["skip"] = []
            room["users"] = []
            room["check_users"] = []
            room["current_beatmap"] = room.get("current_beatmap", None)

            if room.get("bot_mode") == 1:
                self.load_beatmapset(room=room)

    def load_beatmapset(self, room: dict):
        import random

        beatmaps = []

        if not room.get("beatmapset_filename"):
            raise ValueError("beatmapset_filename is required!")

        with open("beatmapsets/" + room.get("beatmapset_filename"), "r") as f:
            beatmaps = json.loads(f.read())

        logger.info(
            f"~ {room.get('name')} | Auto Pick Map Room | {room.get('min')} -> {room.get('max')} | {len(beatmaps)} Total Beatmaps!"
        )

        random.shuffle(beatmaps)
        room["beatmaps"] = beatmaps

    def connect(self, timeout=5.0) -> bool:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)

        try:
            self.socket.connect((self.host, self.port))
            self.send(f"PASS {self.password}")
            self.send(f"NICK {self.username}")
            logger.info(
                f"~ Connected to {self.host}:{self.port} | username: {self.username}"
            )
            return True
        except TimeoutError:
            logger.critical("~ Timeout Error!")
        except socket.gaierror:
            logger.critical("~ No Internet Connection!")

        return False

    def disconnect(self) -> None:
        self.stop = True
        self.socket.close()

    def send(self, message: str) -> None:
        sleep(0.50)
        self.socket.send(f"{message}\n".encode())

    def send_private(self, recipient: str, message: str) -> None:
        self.send(f"PRIVMSG {recipient} : {message}")

    def receive(self, size=2048) -> str:
        return self.socket.recv(size).decode()

    def username_parser(self, username: str) -> str:
        return username.strip().replace(" ", "_")

    def message_parser(self, message: str) -> dict:
        # return: {type: string, sender: string, message: string, room_id: string}
        if message.startswith(":cho.ppy.sh"):
            return {
                "type": "server",
                "sender": "cho.ppy.sh",
                "message": message,
                "room_id": None,
            }

        if "PRIVMSG" in message:
            split_message = message.split(" :")
            sender = self.username_parser(
                split_message[0][1 : split_message[0].rfind("!")]
            )
            sender_message = " :".join(split_message[1:])

            if "PRIVMSG #mp_" in message:
                room_id_index = split_message[0].rfind("#mp_")
                room_id = split_message[0][room_id_index:]
                return {
                    "type": "room",
                    "sender": sender,
                    "message": sender_message,
                    "room_id": room_id,
                }
            else:
                return {
                    "type": "private",
                    "sender": sender,
                    "message": sender_message,
                    "room_id": None,
                }

        return {"type": None, "sender": None, "message": message, "room_id": None}

    def get_room(self, room_name=None, room_id=None) -> dict:
        for room in self.rooms:
            if room.get("name") == room_name or room.get("room_id") == room_id:
                return room

    def close_rooms(self):
        for room in self.rooms:
            if room.get("room_id"):
                self.send_private(room.get("room_id"), "!mp close")
                room["connected"] = False

    def join_rooms(self):
        for room in self.rooms:
            if room.get("room_id"):
                self.send(f"JOIN {room.get('room_id')}")

    def setup_room_settings(self, room: dict) -> None:
        logger.info(f"~ Setting up Room {room.get('name')} | {room.get('room_id')}")

        if room:
            self.send_private(room.get("room_id"), f"!mp name {room.get('name')}")
            self.send_private(
                room.get("room_id"), f"!mp password {room.get('password')}"
            )
            sleep(1)
            self.send_private(
                room.get("room_id"),
                f"!mp set {room.get('team_mode')} {room.get('score_mode')} {room.get('room_size', 16)}",
            )
            self.send_private(room.get("room_id"), "!mp mods Freemod")

    def on_skip_rotate(self, room: dict):
        if room.get("bot_mode") == 0 and room.get("users"):
            room["users"] = room["users"][1:] + room["users"][0:1]
            self.send_private(room.get("room_id"), f"!mp host {room.get('users')[0]}")
        elif room.get("bot_mode") == 1 and room.get("beatmaps"):
            self.send_private(
                room.get("room_id"),
                f"!mp map {room.get('beatmaps')[0].get('beatmap_id')} {room.get('play_mode')}",
            )
            room["beatmaps"] = room["beatmaps"][1:] + room["beatmaps"][0:1]

        room["skip"] = []

    def get_beatmap_info(self, url: str) -> dict | None:
        logger.info(f"~ Fetching url: {url}")
        res = None

        try:
            res = requests.get(url, timeout=(10, 10))
            logger.info(f"~ Fetch status|code: {res.ok} | {res.status_code}")
        except Exception as err:
            logger.critical(f"~ Fetch beatmap info error! | {err}")

        if res.ok:
            beatmap_info = re.search('\{"artist".+', res.text).group(0)

            if beatmap_info:
                try:
                    return json.loads(beatmap_info)
                except json.decoder.JSONDecodeError as err:
                    logger.error(f"~Beatmap info decode error | {err}")

        logger.error("~Beatmap failed to fetch")

    def links(self, title: str, beatmap_id: int) -> str:
        if not beatmap_id:
            return ""

        return f"[https://osu.ppy.sh/beatmapsets/{beatmap_id} {title}] [https://beatconnect.io/b/{beatmap_id}/ beatconnect]"

    def get_queue(self, room: dict) -> str:
        if room.get("bot_mode") == 1:
            message = []

            for beatmap in room.get("beatmaps")[0:5]:
                message.append(
                    f"[https://osu.ppy.sh/b/{beatmap.get('beatmap_id')} {beatmap.get('title')}]"
                )

            return ", ".join(message)
        elif room.get("bot_mode") == 0:
            return ", ".join(room.get("users")[0:5])

    def on_room_created(self, room_name: str, room_id: str):
        if room_name and room_id:
            logger.info(f"~ Room Created {room_id} | {room_name}")
            room = self.get_room(room_name=room_name)

            if room:
                room["room_id"] = room_id
                self.setup_room_settings(room=room)
                self.on_skip_rotate(room=room)

    def on_room_closed(self, room: dict):
        logger.warning(f"~ Room closed | {room.get('name')}")
        room["created"] = room["connected"] = False
        room["users"] = []

    def on_user_joined(self, room: dict, user: str) -> None:
        logger.info(f"~ {user} joined the room {room.get('room_id')}")

        if user not in room.get("users"):
            room["users"].append(user)
            logger.info(f"~ {user} added to {room.get('name')} | {room.get('users')}")

        if room.get("bot_mode") == 0 and len(room.get("users")) == 1:
            self.on_skip_rotate(room=room)

    def on_user_left(self, room: dict, user: str) -> None:
        logger.info(f"~ {user} left the room {room.get('room_id')}")

        # autohost | rotate on host leave
        if (
            room.get("bot_mode") == 0
            and room.get("users")
            and room.get("users")[0] == user
        ):
            self.on_skip_rotate(room=room)

        if user in room.get("users"):
            room["users"].remove(user)

    def on_host_changed(self, room: dict, user: str) -> None:
        logger.info(f"~ room {room.get('room_id')} changed host to {user}")
        room["skip"] = []

        if room.get("bot_mode") == 0 and room.get("users"):
            # host gave host to the second user in queue
            if len(room.get("users")) > 1 and user == room.get("users")[1]:
                logger.info("~ host gave host to the second user in queue")
                room["users"] = room["users"][1:] + room["users"][0:1]
            # host gave the host to random user
            elif user != room.get("users")[0]:
                logger.info("~ host gave the host to random user")
                self.send_private(
                    room.get("room_id"), f"!mp host {room.get('users')[0]}"
                )

    def on_match_started(self, room: dict) -> None:
        logger.info(f"~ room {room.get('room_id')} Match started")
        room["skip"] = []

        if room.get("bot_mode") == 0:
            self.on_skip_rotate(room=room)

    def on_match_finished(self, room: dict) -> None:
        logger.info(f"~ room {room.get('room_id')} Match finished")
        self.send_private(
            room.get("room_id"), f"!mp settings | Queue: {self.get_queue(room=room)}"
        )

        if room.get("bot_mode") == 1:
            self.on_skip_rotate(room=room)

    def on_match_ready(self, room: dict) -> None:
        logger.info(f"~ room {room.get('room_id')} Match ready")
        self.send_private(room.get("room_id"), "!mp start")

    def send_beatmap_violation(self, room: dict, message: str, error: str) -> None:
        self.send_private(
            room.get("room_id"),
            f"!mp map {room.get('current_beatmap')} {room.get('play_mode')} | Rule Violation [{error}]: {message}",
        )

    def set_room_beatmap(self, room: dict, version: str, url: str) -> None:
        if not version or not url:
            self.send_beatmap_violation(
                room,
                "Beatmap not found!",
                "NotFound",
            )
            return
        elif url == "https://osu.ppy.sh/b/0":
            self.send_beatmap_violation(
                room,
                "Beatmap Not Submitted!",
                "NotFound",
            )
            return

        try:
            response = requests.get(url, timeout=(10, 10))
        except Exception as err:
            logger.critical(f"~ Fetch Error: {err}")
            self.send_beatmap_violation(room, "Fetching beatmap error!", "HttpError")
            return

        if not response.ok:
            self.send_beatmap_violation(
                room,
                "Beatmap Not Submitted!",
                "NotFound",
            )
            return

        beatmap_info = re.search('\{"artist".+', response.text).group(0)

        if not beatmap_info:
            self.send_beatmap_violation(
                room,
                "Beatmap details not found!",
                "NotFound",
            )
            return

        try:
            beatmap_info_json = json.loads(beatmap_info)
        except json.decoder.JSONDecodeError as err:
            logger.error(f"DEBUG: BEATMAP JSON LOAD {err}")
            self.send_beatmap_violation(
                room,
                "Beatmap json parser error",
                "NotFound",
            )
            return

        if beatmap_info_json.get("availability").get("download_disabled"):
            self.send_beatmap_violation(
                room,
                "Beatmap is not available!",
                "DownloadDisabled",
            )
            return

        for beatmap in beatmap_info_json.get("beatmaps"):
            if beatmap.get("version") != version:
                continue

            if beatmap.get("difficulty_rating") < room.get("min"):
                self.send_beatmap_violation(
                    room,
                    f"[https://osu.ppy.sh/beatmapsets/{beatmap_info_json.get('id')}#osu/{beatmap.get('id')} {beatmap.get('version')} | {beatmap.get('difficulty_rating')}*] Low Star* Beatmap",
                    "star",
                )
            elif beatmap.get("difficulty_rating") > room.get("max"):
                self.send_beatmap_violation(
                    room,
                    f"[https://osu.ppy.sh/beatmapsets/{beatmap_info_json.get('id')}#osu/{beatmap.get('id')} {beatmap.get('version')} | {beatmap.get('difficulty_rating')}*] High Star* Beatmap",
                    "star",
                )
            else:
                self.send_private(
                    room.get("room_id"),
                    f'Stars: {beatmap.get("difficulty_rating")} | Status: {beatmap.get("status")} | CircleSize: {beatmap.get("cs")} | ApproachRate: {beatmap.get("ar")} | [{beatmap.get("url")} {beatmap_info_json.get("title", "link")}] [https://beatconnect.io/b/{beatmap_info_json.get("id")}/ Beatconnect]',
                )
                room["current_beatmap"] = beatmap.get("id", room.get("current_beatmap"))
            return

        self.send_beatmap_violation(
            room,
            f"Beatmap version not found",
            "NotFound",
        )

    def on_beatmap_changed_to(
        self, room: dict, title: str, version: str, url: str, beatmap_id: int
    ) -> None:
        # beatmap manually pick by user
        logger.info(f"~ Beatmap change to {title} | {url}")
        self.set_room_beatmap(
            room=room,
            url=url,
            version=version,
        )

    def on_changed_beatmap_to(
        self, room: dict, title: str, url: str, beatmap_id: int
    ) -> None:
        logger.info(f"~Change beatmap to {title} | {url} | {beatmap_id}")
        room["skip"] = []
        room["current_beatmap"] = beatmap_id
        beatmap = self.get_beatmap_info(url=url)

        if beatmap:
            self.send_private(
                room.get("room_id"),
                f"Links: {self.links(title, beatmap.get('id', beatmap_id))}",
            )

    def on_error(self, error):
        logger.error(error)

    def on_disconnected(self):
        for room in self.rooms:
            room["connected"] = False

        self.connect()

    def check_rooms(self):
        for room in self.rooms:
            if room.get("room_id") and not room.get("connected"):
                self.send(f"JOIN {room.get('room_id')}")
                room["connected"] = True
            elif not room.get("created"):
                self.send_private("BanchoBot", f"mp make {room.get('name')}")
                room["created"] = True

    def on_slot(
        self,
        room: dict,
        slot: int,
        status: bool,
        user_id: int,
        user: str,
        roles: list,
    ) -> None:
        logger.info(
            f"~ Room {room.get('room_id')} | Slot {slot} | status {status} | user {user} | ID {user_id} | roles {roles}"
        )

        if user not in room.get("users"):
            room["users"].append(user)
        room["check_users"].append(user)

        # remove offline users
        if len(room["check_users"]) >= room["total_users"]:
            for user in room["users"]:
                if user not in room["check_users"]:
                    room["users"].remove(user)

    def on_players(self, room: dict, players: int) -> None:
        logger.info(f"~ {players} players")
        room["total_users"] = players

    def on_skip(self, room: dict, sender: str) -> None:
        if sender in room.get("skip"):
            return

        room["skip"].append(sender)
        current_votes = len(room.get("users"))
        total = round(len(room.get("users")) / 2)

        if current_votes >= total or (
            room.get("bot_mode") == 0
            and room.get("users")
            and sender == room.get("users")[0]
        ):
            self.on_skip_rotate(room=room)
            return

        self.send_private(
            room.get("room_id"), f"Skip voting: {current_votes} / {total}"
        )

    def on_room_message(self, room: dict, sender: str, message: str) -> None:
        logger.info(f"~ room {room.get('room_id')} message | {sender}: {message}")

        if message.startswith("!start"):
            number = message.split("!start")[-1].strip()

            if number.isdigit():
                self.send_private(room.get("room_id"), f"!mp start {number}")
            elif message == "!start":
                self.send_private(room.get("room_id"), "!mp start")
        elif message == "!stop":
            self.send_private(room.get("room_id"), "!mp aborttimer")
        elif message == "!users":
            self.send_private(
                room.get("room_id"), f"Users: {', '.join(room.get('users', []))}"
            )
        elif message == "!skip":
            self.on_skip(room=room, sender=sender)
        elif message == "!queue":
            self.send_private(
                room.get("room_id"), f"Queue: {self.get_queue(room=room)}"
            )
        elif message == "!info":
            if room.get("bot_mode") == 1:
                self.send_private(
                    room.get("room_id"),
                    f"NoHost | {room.get('min')} -> {room.get('max')} | Commands: start <seconds>, stop, queue, skip",
                )

    def on_receive(self, data: dict) -> None:
        type, message = data.get("type"), data.get("message")

        if type == "private":
            if data.get("sender") == "BanchoBot":
                if message.startswith("Created the tournament match"):
                    search_id_name = re.search(
                        "https://osu.ppy.sh/mp/(\d*)? (.*)", message
                    )
                    name, id = search_id_name.group(2), "#mp_" + search_id_name.group(1)
                    self.on_room_created(room_name=name, room_id=id)
        elif type == "room":
            room_id = data.get("room_id")
            room = self.get_room(room_id=room_id)
            sender = data.get("sender")

            if not room and not data.get("sender"):
                return

            if sender == "BanchoBot":
                logger.debug(f"{room.get('skip')}")
                logger.info(f"{type}: {message}")
                if message == "Closed the match":
                    self.on_room_closed(room=room)
                elif "joined in slot" in message:
                    user = self.username_parser(message.split(" joined in slot")[0])
                    self.on_user_joined(room=room, user=user)
                elif message.endswith("left the game."):
                    user = self.username_parser(message.split(" left the game.")[0])
                    self.on_user_left(room=room, user=user)
                elif message.endswith(" became the host."):
                    user = self.username_parser(message.split(" became the host.")[0])
                    self.on_host_changed(room=room, user=user)
                elif message == "The match has started!":
                    self.on_match_started(room=room)
                elif message == "The match has finished!":
                    self.on_match_finished(room=room)
                elif message == "All players are ready":
                    self.on_match_ready(room=room)
                elif message.startswith("Beatmap changed to: "):
                    search = re.search("Beatmap.*?: (.*)? \[(.*?)\] \((.*)?\)", message)
                    self.on_beatmap_changed_to(
                        room=room,
                        version=search.group(2),
                        title=search.group(1),
                        url=search.group(3),
                        beatmap_id=int(search.group(3).split("/")[-1]),
                    )
                elif message.startswith("Changed beatmap to "):
                    if room.get("bot_mode") == 1:
                        message_split = message.split(" ")
                        url = message_split[3]
                        url_split = url.split("/")
                        beatmap_id = url_split[-1]
                        title = "".join(message_split[4:])
                        self.on_changed_beatmap_to(
                            room=room,
                            title=title,
                            beatmap_id=beatmap_id,
                            url=url,
                        )
                elif message.startswith("Slot "):
                    words = message.split()
                    slot = words[1]
                    user_and_roles = url = username = None

                    if words[2] != "Ready":
                        status = " ".join(words[2:4])
                        url = words[4]
                        user_and_roles = " ".join(words[5:])
                    else:
                        status = words[2]
                        url = words[3]
                        user_and_roles = " ".join(words[4:])

                    username = user_and_roles
                    roles = None
                    start_roles_index = user_and_roles.rfind("[")

                    # 50/50 for user who have bracket in name... fvckng names | Fvck REGEX
                    if user_and_roles[-1] == "]" and start_roles_index != -1:
                        username = user_and_roles[0 : start_roles_index - 1]
                        roles = (
                            user_and_roles[start_roles_index + 1 : -1]
                            .replace(" ", "")
                            .split("/")
                        )
                        roles = roles[0:-1] + roles[-1].split(",")

                        for role in roles:
                            if role.strip() not in valid_roles:
                                username = user_and_roles
                                roles = None
                                break

                    username = username.strip().replace(" ", "_")
                    user_id = url.split("/")[-1]

                    self.on_slot(
                        room=room,
                        status=status,
                        slot=int(slot) if slot.isdigit() else 0,
                        user_id=int(user_id) if user_id.isdigit() else 0,
                        user=self.username_parser(username),
                        roles=roles,
                    )
                elif message.startswith("Players: "):
                    self.on_players(room=room, players=int(message.split(" ")[-1]))
            else:
                self.on_room_message(room=room, sender=sender, message=message)

    def start(self):
        buffer = ""

        while True:
            try:
                if self.stop:
                    logger.info("~ Program exited")
                    return

                self.check_rooms()
                message = self.receive()

                if not message:
                    raise TimeoutError("Disconnected from server")

                lines = f"{buffer}{message}".split("\n")

                for line in lines[0:-1]:
                    self.on_receive(self.message_parser(line))

                # unfinished message
                if lines:
                    buffer = lines[-1]
            except Exception as err:
                logger.error(f"~ App Error error: {err}")
                self.on_disconnected()


def get_config(config="config.json") -> dict:
    import json

    f = open(config, "r")
    return json.loads(f.read())


if __name__ == "__main__":
    import logging

    logname = f"logs{datetime.now().strftime('%d-%m-%y %H-%M-%S')}.log"
    formatter = "%(asctime)s : %(name)s : %(levelname)s = %(message)s"
    logging.basicConfig(
        filename=logname,
        format=formatter,
        filemode="w",
    )
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    logger = logging.getLogger("irc.py")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(ch)

    config = get_config()
    irc = OsuIrc(
        username=config.get("username"),
        password=config.get("password"),
        rooms=config.get("rooms"),
    )

    # logger.info(irc.get_beatmap_info(url="https://osu.ppy.sh/b/1745634"))
    connected = irc.connect()

    if connected:
        irc.start()
