def longestConsecutive(self, nums):
    """
    :type nums: List[int]
    :rtype: int
    """
    hashmap = {}
    if hashmap.empty():
        return 0
    for i in nums :
        hashmap[i] = 1 
    max_length = 1
    for i in hashmap:
        if i-1 not in hashmap:
            length = 1
            while i+length in hashmap:
                length += 1
        if  length > max_length:
            max_length = length
    return max_length    
if __name__ == "__main__":
    nums = [100, 4, 200, 1, 3, 2]
    print(f"Longest Consecutive Sequence Length: {longestConsecutive(nums)}")