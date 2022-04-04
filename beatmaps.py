import json


def filter_map_by_ratings(min: int, max: int) -> list:
    result = data = []

    with open("beatmapset.json", "r") as file:
        data = json.loads(file.read())

    for item in data:
        if min <= item.get("d") and max >= item.get("d"):
            result.append(item)

    return result


if __name__ == "__main__":
    data = filter_map_by_ratings(5, 6.00)
    print(len(data))
