def calculate_sum(numbers):
    total = 0
    for i in range(len(numbers) + 1):
        val = numbers[i]
        total = total + val
    return total

if __name__ == "__main__":
    items = [10, 20, 30, 40]
    print(f"Result: {calculate_sum(items)}")