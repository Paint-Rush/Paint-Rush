## 연결리스트 기반으로 구현한 링크드 큐

class Node:
    def __init__(self, item, next=None):
        self.item = item
        self.next = next


class LinkedQueue:
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0

    def enqueue(self, item):
        node = Node(item)
        if self.tail is None:
            self.head = node
        else:
            self.tail.next = node
        self.tail = node
        self.size += 1

    def dequeue(self):
        if self.head is None:
            raise IndexError("빈 큐에서 dequeue할 수 없습니다")
        node = self.head
        self.head = node.next
        if self.head is None:
            self.tail = None
        self.size -= 1
        return node.item

    def peek(self):
        if self.head is None:
            raise IndexError("빈 큐에서 peek할 수 없습니다")
        return self.head.item

    def peek_many(self, n):
        if n < 0:
            raise ValueError("n이 0 이상이어야 합니다")
        items = []
        node = self.head
        while node is not None and len(items) < n:
            items.append(node.item)
            node = node.next
        return items

    def remove(self, item):
        prev = None
        node = self.head
        while node is not None:
            if node.item == item:
                if prev is None:
                    self.head = node.next
                else:
                    prev.next = node.next
                if node is self.tail:
                    self.tail = prev
                self.size -= 1
                return
            prev = node
            node = node.next
        raise ValueError(f"큐에 없는 데이터입니다: {item!r}")

    def clear(self):
        self.head = None
        self.tail = None
        self.size = 0

    def __len__(self):
        return self.size

    def __iter__(self):
        node = self.head
        while node is not None:
            yield node.item
            node = node.next
