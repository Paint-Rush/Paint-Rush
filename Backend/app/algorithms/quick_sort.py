def quick_sort(arr, low, high):
    if low < high:
        pivot_index = partition(arr, low, high)
        quick_sort(arr, low, pivot_index - 1)
        quick_sort(arr, pivot_index + 1, high)


def partition(arr, low, high):
    pivot = arr[high]  # 보통 맨 끝(high)을 피벗으로 설정
    i = low - 1  # 피벗보다 작은 값들이 들어갈 위치

    for j in range(low, high):
        if arr[j] <= pivot: # 요소가 피벗보다 작다면?
            i += 1 # 1 증가
            arr[i], arr[j] = arr[j], arr[i] # 스왑

    # 피벗을 제자리로 이동 (스왑)
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1
