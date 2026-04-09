import threading
import time

def add_to_log(msg, log_list=None):
    if log_list is None:
        log_list = []
    log_list.append(msg)
    return log_list

counter = 0
counter_lock = threading.Lock()

def increment_counter():
    global counter
    for _ in range(100000):
        with counter_lock:
            counter += 1

if __name__ == "__main__":
    print("Log 1:", add_to_log("Task A start"))
    print("Log 2:", add_to_log("Task B start"))

    t1 = threading.Thread(target=increment_counter)
    t2 = threading.Thread(target=increment_counter)
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    print(f"Final Counter: {counter} (Expected: 200000)")