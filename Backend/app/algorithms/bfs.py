## BFS 알고리즘
## 내부에서 쓰인 큐는 미리 만들어둔거 이용

from app.algorithms.queue import LinkedQueue

DIRECTION = ((0, -1), (1, 0), (0, 1), (-1, 0))
PASSABLE_TILES = ("NORMAL", "GLASS")


def is_passable(map_data: dict, x: int, y: int) -> bool :
    return 0 <= x < map_data["width"] and 0 <= y < map_data["height"] and map_data["tiles"][y][x] in PASSABLE_TILES


def find_path(
    map_data: dict,
    start: tuple[int, int],  
    target: tuple[int, int],
    max_steps: int | None = None,
) -> list[ tuple [int, int] ] | None :
    if max_steps is not None and max_steps < 0:
        return None
    parents={start: None}
    steps = {start: 0}
    queue = LinkedQueue()
    queue.enqueue(start)
    while queue:
        current = queue.dequeue()
        if current == target:
            path = []
            while current is not None:
                path.append(current)
                current = parents[current]
            return path[::-1]
        if steps[current] == max_steps:
            continue
        x, y = current
        for dx, dy in DIRECTION:
            nxt = (x + dx, y + dy)
            if nxt not in parents and is_passable(map_data, *nxt):
                parents[nxt] = current
                steps[nxt] = steps[current] + 1
                queue.enqueue(nxt)
    return None
