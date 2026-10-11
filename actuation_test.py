import os
import ctypes
import time
import subprocess

libc = ctypes.CDLL("libc.so.6")
PROT_READ = 1
MAP_SHARED = 1

def check_incore(filepath):
    size = os.path.getsize(filepath)
    if size == 0: return 0.0
    fd = os.open(filepath, os.O_RDONLY)
    
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_longlong]
    addr = libc.mmap(None, size, PROT_READ, MAP_SHARED, fd, 0)
    
    if addr == -1 or addr == 0xFFFFFFFFFFFFFFFF or addr == 0:
        os.close(fd)
        return 0.0
        
    page_size = os.sysconf("SC_PAGE_SIZE")
    num_pages = (size + page_size - 1) // page_size
    vec = ctypes.create_string_buffer(num_pages)
    
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
    libc.mincore(addr, size, vec)
    
    resident_pages = sum(1 for i in range(num_pages) if vec[i][0] & 1)
    
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    libc.munmap(addr, size)
    os.close(fd)
    return resident_pages / num_pages

def main():
    test_file = "/tmp/actuation_test.dat"
    print("Creating 10MB test file...")
    with open(test_file, "wb") as f:
        f.write(os.urandom(10 * 1024 * 1024))
        os.fsync(f.fileno())
    
    print(f"Initial state in cache (just written): {check_incore(test_file):.1%}")
    
    fd = os.open(test_file, os.O_RDONLY)
    
    # 1. Evict (DONTNEED)
    print("\nApplying POSIX_FADV_DONTNEED (Eviction)...")
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    time.sleep(0.1) # Let kernel process it
    print(f"Cache residency after DONTNEED: {check_incore(test_file):.1%}")
    
    # 2. Prefetch (WILLNEED)
    print("\nApplying POSIX_FADV_WILLNEED (Prefetch)...")
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_WILLNEED)
    time.sleep(0.1) # WILLNEED is asynchronous readahead
    print(f"Cache residency after WILLNEED: {check_incore(test_file):.1%}")
    
    # 3. Read it fully
    print("\nPerforming standard full read()...")
    with open(test_file, "rb") as f:
        f.read()
    print(f"Cache residency after full read: {check_incore(test_file):.1%}")
    
    # 4. Evict again
    print("\nApplying POSIX_FADV_DONTNEED again...")
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    print(f"Cache residency after final DONTNEED: {check_incore(test_file):.1%}")
    
    os.close(fd)
    os.remove(test_file)

if __name__ == "__main__":
    main()
