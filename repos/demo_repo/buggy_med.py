def process_user_data(data_list):
    results = []
    for entry in data_list:
        if "score" not in entry:
            results.append(f"{entry['name']}: No Score")
            continue
        
        score = entry["score"]
        if isinstance(score, str):
            score = int(score)
        new_score = score + 10
        
        if new_score > 100:
            results.append(f"{entry['name']}: Passed")
        else:
            results.append(f"{entry['name']}: Failed")
    
    return results

if __name__ == "__main__":
    raw_data = [
        {"name": "Alice", "score": "95"},
        {"name": "Bob", "score": 50},
        {"name": "Charlie"}
    ]
    processed = process_user_data(raw_data)
    print(f"Processed Results: {processed}")