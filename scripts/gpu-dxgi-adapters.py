#!/usr/bin/env python3
"""Read DXGI adapter order used by DirectML device_id; no model/GPU workload."""
import ctypes
from ctypes import wintypes
import json
import uuid

class GUID(ctypes.Structure):
    _fields_=[('Data1',ctypes.c_uint32),('Data2',ctypes.c_uint16),('Data3',ctypes.c_uint16),('Data4',ctypes.c_ubyte*8)]
class LUID(ctypes.Structure):
    _fields_=[('LowPart',wintypes.DWORD),('HighPart',wintypes.LONG)]
class AdapterDesc1(ctypes.Structure):
    _fields_=[('Description',wintypes.WCHAR*128),('VendorId',wintypes.UINT),('DeviceId',wintypes.UINT),('SubSysId',wintypes.UINT),('Revision',wintypes.UINT),('DedicatedVideoMemory',ctypes.c_size_t),('DedicatedSystemMemory',ctypes.c_size_t),('SharedSystemMemory',ctypes.c_size_t),('AdapterLuid',LUID),('Flags',wintypes.UINT)]

def method(ptr,index,result,*args):
    table=ctypes.cast(ptr,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result,ctypes.c_void_p,*args)(table[index])

def adapters():
    dxgi=ctypes.WinDLL('dxgi')
    factory=ctypes.c_void_p()
    iid=GUID.from_buffer_copy(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    create=dxgi.CreateDXGIFactory1
    create.argtypes=[ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)];create.restype=ctypes.c_long
    code=create(ctypes.byref(iid),ctypes.byref(factory))
    if code<0:raise OSError(hex(code & 0xffffffff))
    rows=[]
    try:
        enum=method(factory,12,ctypes.c_long,wintypes.UINT,ctypes.POINTER(ctypes.c_void_p))
        for index in range(32):
            adapter=ctypes.c_void_p();code=enum(factory,index,ctypes.byref(adapter))
            if code & 0xffffffff==0x887a0002:break
            if code<0:raise OSError(hex(code & 0xffffffff))
            try:
                desc=AdapterDesc1();code=method(adapter,10,ctypes.c_long,ctypes.POINTER(AdapterDesc1))(adapter,ctypes.byref(desc))
                if code<0:raise OSError(hex(code & 0xffffffff))
                rows.append({'device_id':index,'name':desc.Description,'vendor_id':desc.VendorId,'device_pci_id':desc.DeviceId,'dedicated_video_bytes':desc.DedicatedVideoMemory,'software':bool(desc.Flags & 2),'luid':f'{desc.AdapterLuid.HighPart & 0xffffffff:08x}:{desc.AdapterLuid.LowPart:08x}'})
            finally:method(adapter,2,wintypes.ULONG)(adapter)
    finally:method(factory,2,wintypes.ULONG)(factory)
    return rows

if __name__=='__main__':
    print(json.dumps({'api':'IDXGIFactory1.EnumAdapters1 / IDXGIAdapter1.GetDesc1','adapters':adapters()},ensure_ascii=False,indent=2))
