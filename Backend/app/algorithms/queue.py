### 10503 길명원 ###
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
        self.size = self.size + 1

    def dequeue(self):
        if self.head is None:
            raise IndexError("빈 큐에서는 dequeue X")
        node = self.head
        self.head = node.next
        if self.head is None:
            self.tail = None
        self.size = self.size - 1
        return node.item

    def peek(self):
        if self.head is None:
            raise IndexError("빈 큐에서는 peek X")
        return self.head.item

    def peek_many(self, n):
        if n < 0:
            raise ValueError("n이 0 이상이어야함")
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
                self.size = self.size - 1
                return
            prev = node
            node = node.next
        raise ValueError(f"큐에 없는 데이터:  {item!r}")

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

## 원형으로 안한 이유는 이걸 매칭시스템에 쓸건데 매칭 대기 인원은 일정하지 않으니 유저가 올때마다 노드만 추가하면 되는 링크드로 구현
## 글고 파이썬에서는 가비지 컬렉터가 열일을 해주기에 표류현상 X
