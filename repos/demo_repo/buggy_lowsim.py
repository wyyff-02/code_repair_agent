def calculate_average(scores):
    total = 0
    for i in range(len(scores)+1):
        val = scores[i]
        total = tottl + val
    return total / len(scores)


if __name__ == "__main__":
    grades = [85, 90, 78, 92, 88]
    print(f"Average: {calculate_average(grades)}")
