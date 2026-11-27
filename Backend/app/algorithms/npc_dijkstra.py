"""Prototype NPC pathfinding. The caller supplies terrain and live actors."""

from heapq import heappop, heappush
from math import inf
from random import choice


WALKABLE = {"NORMAL", "GLASS"}
DIRECTIONS = ((0, -1), (1, 0), (0, 1), (-1, 0))
NPC_MOVE_INTERVAL_SECONDS = 2.0


def chase_tick(grid, npc, actors):
    """Run one movement step on the caller's NPC timer (2 seconds by default).

    grid[y][x] is terrain. Each actor has an (x, y) position and an alive flag.
    Return True if the NPC moved. A blocked route simply returns False.
    """
    if not grid or not grid[0] or not npc.alive:
        return False

    height, width = len(grid), len(grid[0])

    def can_walk(x, y):
        return (
            0 <= x < width
            and 0 <= y < height
            and grid[y][x] in WALKABLE
        )

    start = tuple(npc.position)
    sx, sy = start
    if not can_walk(sx, sy):
        return False

    candidates = []
    for actor in actors:
        if actor is npc or not actor.alive:
            continue
        x, y = actor.position
        if can_walk(x, y):
            candidates.append((x, y))

    if not candidates:
        return False

    # 좌표는 (x, y), 배열 접근은 [y][x].
    distance = [[inf] * width for _ in range(height)]
    previous = {}
    distance[sy][sx] = 0
    queue = [(0, sx, sy)]

    while queue:
        cost, x, y = heappop(queue)
        if cost != distance[y][x]:
            continue

        for dx, dy in DIRECTIONS:
            nx, ny = x + dx, y + dy
            if not can_walk(nx, ny):
                continue

            new_cost = cost + 1
            if new_cost < distance[ny][nx]:
                distance[ny][nx] = new_cost
                previous[nx, ny] = (x, y)
                heappush(queue, (new_cost, nx, ny))

    shortest = min(distance[y][x] for x, y in candidates)
    if shortest == inf or shortest == 0:
        return False

    nearest = [
        (x, y) for x, y in candidates
        if distance[y][x] == shortest
    ]
    target = choice(nearest)

    # 목표에서 경로를 거꾸로 따라가 시작점 바로 다음 칸을 찾는다.
    next_tile = target
    while previous[next_tile] != start:
        next_tile = previous[next_tile]

    npc.position = next_tile
    return True
