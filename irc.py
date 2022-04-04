from email import message
import json
import re
import socket
from time import sleep

from beatmaps import filter_map_by_ratings

team_mode = {0: "HeadToHead", 1: "TagCoop", 2: "TeamVs", 3: "TagTeamVs"}
score_mode = {0: "Score", 1: "Accuracy", 2: "Combo", 3: "ScoreV2"}
play_mode = {0: "osu!", 1: "Taiko", 2: "Catch the Beat", 3: "osu!Mania"}
bot_mode = {0: "AutoHost", 1: "AutoRoom"}
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
            room["total_users"] = room["skip"] = 0
            room["users"] = []
            room["check_users"] = []
            room["current_beatmap"] = room.get("current_beatmap", None)

            if room.get("bot_mode") == 1:
                import random

                print(
                    f"~ {room.get('name')} | Auto Pick Map Room | {room.get('min')} -> {room.get('max')}"
                )
                beatmaps = filter_map_by_ratings(
                    min=room.get("min", 0), max=room.get("max", 10)
                )
                print(f"~ {len(beatmaps)} Total Beatmaps!")

                random.shuffle(beatmaps)
                room["beatmaps"] = beatmaps

    def connect(self, timeout=5.0) -> bool:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)

        try:
            self.socket.connect((self.host, self.port))
            self.send(f"PASS {self.password}")
            self.send(f"NICK {self.username}")
            print(f"~ Connected to {self.host}:{self.port} | username: {self.username}")
            return True
        except TimeoutError:
            print("~ Timeout Error!")
        except socket.gaierror:
            print("~ No Internet Connection!")

        return False

    def disconnect(self) -> None:
        self.stop = True
        self.socket.close()

    def check_beatmap(self, room: dict, beatmap_id: int):
        room["current_beatmap"] = beatmap_id

        self.send_private(
            room.get("room_id"), f"!mp map {beatmap_id} {room.get('play_mode')}"
        )

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
        # message: str
        # return: dict {sender, message, type: none | private | room, room_id}

        # test
        # :username!cho@ppy.sh PRIVMSG #mp_99211675 : message -> room
        # :BanchoBot!cho@ppy.sh PRIVMSG username :Created the tournament match https://osu.ppy.sh/mp/99999999 room 1 -> private
        # :cho.ppy.sh message -> server
        if message.startswith(":cho.ppy.sh"):
            return {
                "sender": "cho.ppy.sh",
                "message": message,
                "type": "server",
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
                    "room_id": room_id,
                    "message": sender_message,
                }
            else:
                return {"message": sender_message, "type": "private", "sender": sender}

        return {"message": message, "type": None}

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
        print(f"Setting up Room {room.get('name')} {room.get('room_id')}")

        if room:
            self.send_private(room.get("room_id"), f"!mp name {room.get('name')}")
            self.send_private(
                room.get("room_id"), f"!mp password {room.get('password')}"
            )
            sleep(2)
            self.send_private(
                room.get("room_id"),
                f"!mp set {room.get('team_mode')} {room.get('score_mode')} {room.get('room_size', 16)}",
            )
            self.send_private(room.get("room_id"), "!mp mods Freemod")

    def on_skip_rotate(self, room: dict):
        if room.get("bot_mode") == 1:
            self.send_private(
                room.get("room_id"),
                f"!mp map {room.get('beatmaps')[0].get('b')} {room.get('play_mode')}",
            )
            room["beatmaps"] = room["beatmaps"][1:] + room["beatmaps"][0:1]
        elif room.get("bot_mode") == 0:
            if room["users"]:
                room["users"] = room["users"][1:] + room["users"][0:1]
                self.send_private(
                    room.get("room_id"), f"!mp host {room.get('users')[0]}"
                )

    def get_beatmap_info(self, url: str) -> dict | None:
        print(f"~Fetching: {url}")
        import requests

        res = None

        try:
            res = requests.get(url, timeout=(10, 10))
        except requests.exceptions.HTTPError as err:
            print("!Fetching beatmap info error! ", err)
            return

        print("~Fetch status", res.ok, res.status_code)

        if res.ok:
            beatmap_info = re.search('\{"artist".+', res.text).group(0)

            if beatmap_info:
                try:
                    return json.loads(beatmap_info)
                except json.decoder.JSONDecodeError as err:
                    print(f"~Beatmap info decode error", {err})
        print("~Beatmap failed to fetch")

    def links(self, title: str, beatmap_id: int) -> str:
        return f"[https://osu.ppy.sh/beatmapsets/{beatmap_id} {title}] [https://beatconnect.io/b/{beatmap_id}/ beatconnect]"

    def get_queue(self, room: dict) -> str:
        if room.get("bot_mode") == 1:
            message = []

            for beatmap in room.get("beatmaps")[0:5]:
                message.append(
                    f"[https://osu.ppy.sh/b/{beatmap.get('b')} {beatmap.get('t')}]"
                )

            return "  ".join(message)
        elif room.get("bot_mode") == 0:
            return ", ".join(room.get("users")[0:5])

    def on_room_created(self, room_name: str, room_id: str):
        if room_name and room_id:
            print(f"Room Created {room_id} {room_name}")
            room = self.get_room(room_name=room_name)

            if room:
                room["room_id"] = room_id
                self.setup_room_settings(room=room)
                self.on_skip_rotate(room=room)

    def on_room_closed(self, room: dict):
        room["created"] = room["connected"] = room["connected"] = False
        room["users"] = []

    def on_user_joined(self, room: dict, user: str) -> None:
        print(f"~ {user} joined the room {room.get('room_id')}")

        if user not in room["users"]:
            room["users"].append(user)
            print(f"{user} added to {room.get('users')}")

        if len(room.get("users")) == 1 and room.get("bot_mode") == 0:
            self.on_skip_rotate(room=room)

    def on_user_left(self, room: dict, user: str) -> None:
        print(f"~ {user} left the room {room.get('room_id')}")

        # host quit
        if room.get("users")[0] == user and room.get("bot_mode") == 0:
            self.on_skip_rotate(room=room)

        if user in room.get("users"):
            room["users"].remove(user)

    def on_host_changed(self, room: dict, user: str) -> None:
        print(f"~ room {room.get('room_id')} changed host to {user}")

        if room.get("bot_mode") == 0:
            # host gave host to the second user in queue
            if len(room.get("users")) >= 2 and user == room.get("users")[1]:
                print("~ host gave host to the second user in queue")
                room["users"] = room["users"][1:] + room["users"][0:1]
            # host gave the host to random user
            elif user != room.get("users")[0]:
                print("~ host gave the host to random user")
                self.send_private(
                    room.get("room_id"), f"!mp host {room.get('users')[0]}"
                )

    def on_match_started(self, room: dict) -> None:
        print(f"~ room {room.get('room_id')} Match started")

        if room.get("bot_mode") == 0:
            self.on_skip_rotate(room=room)

    def on_match_finished(self, room: dict) -> None:
        print(f"~ room {room.get('room_id')} Match finished")
        self.send_private(
            room.get("room_id"), f"!mp settings | Users: {self.get_queue(room=room)}"
        )

        if room.get("bot_mode") == 1:
            self.on_skip_rotate(room=room)

    def on_match_ready(self, room: dict) -> None:
        print(f"~ room {room.get('room_id')} Match ready")
        self.send_private(room.get("room_id"), "!mp start")

    def on_beatmap_changed_to(
        self, room: dict, title: str, version: str, url: str, beatmap_id: int
    ) -> None:
        # beatmap manually pick by user
        print(f"~Beatmap change to {title}")
        self.set_room_beatmap(
            room=room,
            url=url,
            version=version,
        )

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

        import requests

        try:
            response = requests.get(url, timeout=(10, 10))
        except requests.exceptions.HTTPError as err:
            self.send_beatmap_violation(room, err, "HttpError")
            return
        except requests.exceptions.ReadTimeout:
            self.send_private(
                room.get("room_id"),
                f'ReadTimeout | Beatmap Checking error. Slow internet connection... | [https://beatconnect.io/b/{url.split("/")[-1]}/ Beatconnect]',
            )
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
            print(f"DEBUG: BEATMAP JSON LOAD {err}")
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

    # test
    def on_changed_beatmap_to(
        self, room: dict, title: str, url: str, beatmap_id: int
    ) -> None:
        print(f"~Change beatmap to {title} | {url} | {beatmap_id}")
        room["current_beatmap"] = beatmap_id
        beatmap = self.get_beatmap_info(url=url)

        if beatmap:
            self.send_private(
                room.get("room_id"),
                f"Links: {self.links(title, beatmap.get('id', beatmap_id))}",
            )

    def on_error(self, error):
        print(error)

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
        print(
            f"~ room {room.get('room_id')} Slot: {slot} Status: {status} {user} [ID: {user_id}] roles: {roles}"
        )

        if user not in room["users"]:
            room["users"].append(user)
        room["check_users"].append(user)

        # remove offline users
        if len(room["check_users"]) >= room["total_users"]:
            for user in room["users"]:
                if user not in room["check_users"]:
                    room["users"].remove(user)

    def on_players(self, room: dict, players: int) -> None:
        print(f"~ {players} players")
        room["total_users"] = players

    def on_skip(self, room: dict, sender: str) -> None:
        room["skip"] += 1
        total = round(len(room.get("users")) / 2)

        if room.get("skip") >= total or (
            room.get("bot_mode") == 0 and sender == room.get("users")[0]
        ):
            self.on_skip_rotate(room=room)
            return

        self.send_private(
            room.get("room_id"), f"Skip voting: {room.get('skip')}/{total}"
        )

    def on_room_message(self, room: dict, sender: str, message: str) -> None:
        print(f"~ room {room.get('room_id')} message | {sender}: {message}")

        if message.startswith("!start"):
            number = message.split("!start")[-1].strip()

            if number.isdigit():
                self.send_private(room.get("room_id"), f"!mp start {number}")
            elif message == "!start":
                self.send_private(room.get("room_id"), "!mp start")
        elif message == "!stop":
            self.send_private(room.get("room_id"), "!mp aborttimer")
        elif message == "!users":
            self.send_private(room.get("room_id"), f"Users: {room.get('users')}")
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
                    f"NoHost | {room.get(min)} -> {room.get('max')} | peepee random map | Commands: start <seconds>, stop, queue, skip |  ",
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
                if message == "Closed the match":
                    self.on_room_closed(room=room)
                elif "joined in slot" in message:
                    user = self.username_parser(message.split(" joined in slot")[0])
                    self.on_user_joined(room=room, user=user)
                elif message.endswith("left the game."):
                    user = self.username_parser(message.split(" left the game.")[0])
                    self.on_user_left(room=room, user=user)
                elif message.endswith(" became the host."):
                    room["skip"] = 0
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
                    url = user_and_roles = username = None

                    if words[2] != "Ready":
                        status = " ".join(words[2:4])
                        url = words[4]
                        user_and_roles = " ".join(words[5:])
                    else:
                        status = words[2]
                        url = words[3]
                        user_and_roles = " ".join(words[4:])

                    if user_and_roles[-1] == "]":
                        start_roles_index = user_and_roles.rfind("[")
                        username = user_and_roles[0 : start_roles_index - 1]
                        roles = user_and_roles[start_roles_index + 1 : -1].replace(
                            " ", ""
                        )

                        if roles:
                            roles = roles.split("/")

                            if "," in roles[-1]:
                                roles = roles[0:-1] + roles[-1].split(",")

                            for role in roles:
                                if role.strip() not in valid_roles:
                                    username = user_and_roles
                                    roles = None
                                    break
                        else:
                            roles = None

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
                    print("~ Program exited")
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

            except TimeoutError:
                print("~ Timeout Error!")
                self.on_disconnected()
            except socket.gaierror:
                print("~ Connection Error!")
                self.on_disconnected()


def get_config(config="config.json") -> dict:
    import json

    f = open(config, "r")
    return json.loads(f.read())


if __name__ == "__main__":
    config = get_config()
    irc = OsuIrc(
        username=config.get("username"),
        password=config.get("password"),
        rooms=config.get("rooms"),
    )

    # print(irc.get_beatmap_info(url="https://osu.ppy.sh/b/1745634"))
    connected = irc.connect()

    if connected:
        irc.start()
