## CLI 맵 에디터 코드
## 실행방법은 
## cd Backend
## python map_editor.py(mac에선 좀 다를수도? 근데 난 윈도우라 그런거 몰루)
## 나머지는 코드 보고 알아서 하셈 ㅋㅋ

from app.maps import TILE_TYPES, create_default_map, list_maps, load_map, save_map, set_tile

SYMBOLS = {"NORMAL": "N", "GLASS": "G", "BLOCKED": "B"}

HELP = f"""명령어:
  new <map_id> [name]   새 20x20 맵 생성
  load <map_id>         저장된 맵 불러오기
  show                  맵 출력
  set <x> <y> <tile>    타일 수정 ({', '.join(TILE_TYPES)})
  save                  맵 저장
  help                  도움말
  quit                  종료"""


def render(map_data: dict) -> str:
    header = "   " + "".join(str(x % 10) for x in range(map_data["width"]))
    rows = [f"{y:2} " + "".join(SYMBOLS[tile] for tile in row) for y, row in enumerate(map_data["tiles"])]
    return "\n".join([f"[{map_data['map_id']}] {map_data['name']}", header, *rows])


def run_command(current: dict | None, args: list[str]) -> dict | None:
    command = args[0].lower()

    if command == "new" and len(args) >= 2:
        if args[1] in list_maps():
            raise ValueError(f"이미 존재하는 맵 ID입니다: {args[1]}")
        current = create_default_map(args[1], " ".join(args[2:]) or args[1])
        print(render(current))
    elif command == "load" and len(args) == 2:
        current = load_map(args[1])
        print(render(current))
    elif command in ("show", "set", "save") and current is None:
        raise ValueError("먼저 new 또는 load로 맵을 준비하세요.")
    elif command == "show" and len(args) == 1:
        print(render(current))
    elif command == "set" and len(args) == 4:
        try:
            x, y = int(args[1]), int(args[2])
        except ValueError:
            raise ValueError("좌표는 정수여야 합니다.")
        set_tile(current, x, y, args[3].upper())
        print(f"({x}, {y}) -> {args[3].upper()}")
    elif command == "save" and len(args) == 1:
        print(f"저장 완료: {save_map(current, overwrite=True)}")
    elif command == "help":
        print(HELP)
    else:
        raise ValueError("잘못된 명령어입니다. help를 입력하세요.")
    return current


def main() -> None:
    print(HELP)
    current = None
    while True:
        try:
            args = input("map> ").split()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not args:
            continue
        if args[0].lower() == "quit":
            break
        try:
            current = run_command(current, args)
        except (ValueError, OSError) as e:
            print(f"오류: {e}")


if __name__ == "__main__":
    main()
