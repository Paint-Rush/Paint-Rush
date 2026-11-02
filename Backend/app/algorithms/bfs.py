## BFS 알고리즘
## 내부에서 쓰인 큐는 미리 만들어둔거 이용

from app.algorithms.queue import LinkedQueue

DIRECTION = ((0, -1), (1,0), (0,1), (-1,0))
POSSIBLE = ("NORMAL", "GLASS")

def is_possible(map_data: dict, x: int, y: int) -> bool:
    return 0<=x<map_data["width"] and 0<=y<map_data["height"] and map_data["tiles"][y][x] in POSSIBLE

def find_path(map_data: dict, start: tuple[int, int], target: tuple[int, int], max_step: int | None = None) -> list[tuple[int,int]] | None :
    if max_step is not None and max_step < 0:
        return None
    parent = {start: None}
    step = {start: 0}
    queue = LinkedQueue()
    queue.enqueue(start)

    while queue:
        current = queue.dequeue()
        if current == target:
            path = []
            while current is not None:
                path.append(current)
                current = parent[current]
            return path[::-1]
        if step[current] == max_step:
            continue
        x,y = current
        for dx, dy in DIRECTION:
            nxt = (x + dx, y + dy)
            if nxt not in parent and is_possible(map_data, *nxt):
                parent[nxt] = current
                step[nxt] = step[current] +1
                queue.enqueue(nxt)

    return None
