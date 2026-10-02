#define NOMINMAX
#include <windows.h>
#include <oleauto.h>
#include <objbase.h>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <iomanip>
#include <algorithm>
#include <fstream>

using InitializeFn = int (__cdecl*)();
using ReleaseFn = int (__cdecl*)();
using GetDeviceInfoFn = int (__cdecl*)(SAFEARRAY**, SAFEARRAY**);
using GetLedInfoFn = int (__cdecl*)(BSTR, DWORD, BSTR*, SAFEARRAY**);
using GetLedColorFn = int (__cdecl*)(BSTR, DWORD, DWORD*, DWORD*, DWORD*);

static int call_init(InitializeFn fn,DWORD* seh){
    *seh=0;int code=999999;
    __try{code=fn();}__except(*seh=GetExceptionCode(),EXCEPTION_EXECUTE_HANDLER){code=999998;}
    return code;
}
static int call_info(GetDeviceInfoFn fn,SAFEARRAY** a,SAFEARRAY** b,DWORD* seh){
    *seh=0;int code=999999;
    __try{code=fn(a,b);}__except(*seh=GetExceptionCode(),EXCEPTION_EXECUTE_HANDLER){code=999998;}
    return code;
}
static int call_ledinfo(GetLedInfoFn fn,BSTR type,DWORD idx,BSTR* led,SAFEARRAY** styles,DWORD* seh){
    *seh=0;int code=999999;
    __try{code=fn(type,idx,led,styles);}__except(*seh=GetExceptionCode(),EXCEPTION_EXECUTE_HANDLER){code=999998;}
    return code;
}
static int call_color(GetLedColorFn fn,BSTR type,DWORD idx,DWORD* r,DWORD* g,DWORD* b,DWORD* seh){
    *seh=0;int code=999999;
    __try{code=fn(type,idx,r,g,b);}__except(*seh=GetExceptionCode(),EXCEPTION_EXECUTE_HANDLER){code=999998;}
    return code;
}

static std::string utf8(const std::wstring& w){
    if(w.empty()) return {};
    int n=WideCharToMultiByte(CP_UTF8,0,w.data(),(int)w.size(),nullptr,0,nullptr,nullptr);
    std::string out(n,'\0');
    WideCharToMultiByte(CP_UTF8,0,w.data(),(int)w.size(),out.data(),n,nullptr,nullptr);
    return out;
}
static std::string esc(const std::wstring& w){
    std::string s=utf8(w),o; o.reserve(s.size()+8);
    const char* hex="0123456789abcdef";
    for(unsigned char c:s){
        switch(c){
            case '"':o+="\\\"";break; case '\\':o+="\\\\";break;
            case '\b':o+="\\b";break; case '\f':o+="\\f";break;
            case '\n':o+="\\n";break; case '\r':o+="\\r";break; case '\t':o+="\\t";break;
            default:
                if(c<0x20){o+="\\u00";o+=hex[c>>4];o+=hex[c&15];}
                else o+=(char)c;
        }
    }
    return o;
}
static std::vector<std::wstring> arrayStrings(SAFEARRAY* a){
    std::vector<std::wstring> out;
    if(!a) return out;
    LONG lo=0,hi=-1;
    if(SafeArrayGetDim(a)!=1 || FAILED(SafeArrayGetLBound(a,1,&lo)) || FAILED(SafeArrayGetUBound(a,1,&hi))) return out;
    for(LONG i=lo;i<=hi;i++){
        BSTR b=nullptr;
        if(SUCCEEDED(SafeArrayGetElement(a,&i,&b))){
            out.emplace_back(b?std::wstring(b,SysStringLen(b)):L"");
            if(b) SysFreeString(b);
        }
    }
    return out;
}
static unsigned long long fnv(const std::wstring& s){
    unsigned long long h=1469598103934665603ULL;
    for(wchar_t c:s){h^=(unsigned short)c;h*=1099511628211ULL;}
    return h;
}
struct Device{std::wstring type;int leds=0;std::vector<std::wstring> ledNames;};
static std::string hex8(unsigned long long v){std::ostringstream o;o<<std::hex<<std::setfill('0')<<std::setw(16)<<v;return o.str();}
static void writeStage(const std::wstring& path,const std::string& stage,long long code=0,const std::string& detail=""){
    if(path.empty())return;
    std::ofstream out(path,std::ios::binary|std::ios::trunc);
    if(!out)return;
    out<<"{\"native_helper\":true,\"stage\":\""<<stage<<"\",\"code\":"<<code<<",\"detail\":\"";
    for(char c:detail){if(c=='"'||c=='\\')out<<'\\';if(c=='\n')out<<"\\n";else if(c!='\r')out<<c;}
    out<<"\"}";
}

int wmain(int argc,wchar_t** argv){
    std::ios::sync_with_stdio(false);
    if(argc<2){std::cout<<"{\"ok\":false,\"fatal\":true,\"error\":\"DLL MSI manquante\"}\n"<<std::flush;return 2;}
    std::wstring dllPath=argv[1];
    std::wstring stagePath=argc>=3?argv[2]:L"";
    writeStage(stagePath,"process_started",0);
    auto slash=dllPath.find_last_of(L"\\/");
    if(slash!=std::wstring::npos){
        std::wstring dllDir=dllPath.substr(0,slash);
        SetDllDirectoryW(dllDir.c_str());
        SetCurrentDirectoryW(dllDir.c_str());
    }

    HRESULT co=CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);
    writeStage(stagePath,"com_initialized",(long long)co);
    HMODULE mod=LoadLibraryW(dllPath.c_str());
    if(!mod){
        auto err=GetLastError();writeStage(stagePath,"load_library_failed",err);
        std::cout<<"{\"ok\":false,\"fatal\":true,\"diagnostic\":{\"native_helper\":true,\"load_error\":"<<err<<"},\"error\":\"Impossible de charger la DLL MSI\"}\n"<<std::flush;
        if(SUCCEEDED(co)) CoUninitialize(); return 3;
    }
    auto init=(InitializeFn)GetProcAddress(mod,"MLAPI_Initialize");
    auto release=(ReleaseFn)GetProcAddress(mod,"MLAPI_Release");
    auto getInfo=(GetDeviceInfoFn)GetProcAddress(mod,"MLAPI_GetDeviceInfo");
    auto getLedInfo=(GetLedInfoFn)GetProcAddress(mod,"MLAPI_GetLedInfo");
    auto getColor=(GetLedColorFn)GetProcAddress(mod,"MLAPI_GetLedColor");
    if(!init||!getInfo||!getLedInfo||!getColor){
        writeStage(stagePath,"exports_missing",1);
        std::cout<<"{\"ok\":false,\"fatal\":true,\"diagnostic\":{\"native_helper\":true,\"exports_ok\":false},\"error\":\"Fonctions MSI SDK manquantes\"}\n"<<std::flush;
        FreeLibrary(mod); if(SUCCEEDED(co)) CoUninitialize(); return 4;
    }
    writeStage(stagePath,"before_initialize",0);
    DWORD sehCode=0; int initCode=call_init(init,&sehCode);
    writeStage(stagePath,"after_initialize",initCode,sehCode?("SEH "+std::to_string(sehCode)):"");
    if(sehCode){
        std::cout<<"{\"ok\":false,\"fatal\":true,\"diagnostic\":{\"native_helper\":true,\"initialize_exception\":"<<sehCode<<"},\"error\":\"Exception native pendant MLAPI_Initialize\"}\n"<<std::flush;
        FreeLibrary(mod); if(SUCCEEDED(co)) CoUninitialize(); return 5;
    }
    if(initCode!=0){
        std::cout<<"{\"ok\":false,\"fatal\":true,\"diagnostic\":{\"native_helper\":true,\"initialize_code\":"<<initCode<<"},\"error\":\"MLAPI_Initialize a retourne "<<initCode<<"\"}\n"<<std::flush;
        FreeLibrary(mod); if(SUCCEEDED(co)) CoUninitialize(); return 6;
    }

    // Several working Mystic Light integrations wait after MLAPI_Initialize
    // before querying devices. MSI Center may need a short time to expose its
    // service/COM state; querying immediately has been observed to terminate
    // the vendor DLL on some systems.
    writeStage(stagePath,"msi_warmup",0,"1500 ms");
    Sleep(1500);

    std::vector<Device> devices;
    auto scan=[&](int& infoCode,DWORD& infoSeh)->bool{
        SAFEARRAY* types=nullptr;SAFEARRAY* counts=nullptr;infoCode=999999;infoSeh=0;
        for(int attempt=1;attempt<=5;attempt++){
            writeStage(stagePath,"before_get_device_info",attempt,"attempt "+std::to_string(attempt));
            infoCode=call_info(getInfo,&types,&counts,&infoSeh);
            writeStage(stagePath,"after_get_device_info",infoCode,infoSeh?("SEH "+std::to_string(infoSeh)):("attempt "+std::to_string(attempt)));
            if(infoSeh||infoCode==0)break;
            if(types){SafeArrayDestroy(types);types=nullptr;}
            if(counts){SafeArrayDestroy(counts);counts=nullptr;}
            Sleep(1000);
        }
        if(infoSeh||infoCode!=0){if(types)SafeArrayDestroy(types);if(counts)SafeArrayDestroy(counts);return false;}
        auto names=arrayStrings(types);auto nums=arrayStrings(counts);
        if(types)SafeArrayDestroy(types);if(counts)SafeArrayDestroy(counts);
        devices.clear();
        size_t n=std::min(names.size(),nums.size());
        for(size_t di=0;di<n;di++){
            int count=0;try{count=std::stoi(nums[di]);}catch(...){continue;}
            if(count<0||count>4096)continue;
            Device d;d.type=names[di];d.leds=count;d.ledNames.resize(count);
            BSTR type=SysAllocStringLen(d.type.data(),(UINT)d.type.size());
            for(int li=0;li<count;li++){
                writeStage(stagePath,"before_get_led_info",(long long)li,utf8(d.type));
                BSTR led=nullptr;SAFEARRAY* styles=nullptr;DWORD ex=0;
                int c=call_ledinfo(getLedInfo,type,(DWORD)li,&led,&styles,&ex);
                writeStage(stagePath,"after_get_led_info",(long long)c,utf8(d.type)+" #"+std::to_string(li));
                if(c==0&&led)d.ledNames[li]=std::wstring(led,SysStringLen(led));
                else d.ledNames[li]=L"LED "+std::to_wstring(li+1);
                if(led)SysFreeString(led);if(styles)SafeArrayDestroy(styles);
            }
            SysFreeString(type);devices.push_back(std::move(d));
        }
        writeStage(stagePath,"scan_complete",(long long)devices.size());
        return true;
    };

    int infoCode=999999;DWORD infoSeh=0;
    writeStage(stagePath,"ready_for_command",initCode);
    std::string cmd;
    while(std::getline(std::cin,cmd)){
        if(cmd=="SCAN" || devices.empty()) scan(infoCode,infoSeh);
        std::ostringstream out;
        out<<"{\"ok\":"<<(infoCode==0&&infoSeh==0?"true":"false")<<",\"fatal\":false,\"devices\":[";
        for(size_t di=0;di<devices.size();di++){
            if(di)out<<",";
            auto& d=devices[di];std::string id="msi:"+hex8(fnv(d.type));
            out<<"{\"id\":\""<<id<<"\",\"provider\":\"msi\",\"model\":\"MSI "<<esc(d.type)<<"\",\"name\":\""<<esc(d.type)<<"\",\"serial\":\"\",\"type\":0,\"native_name\":\""<<esc(d.type)<<"\",\"positions\":[";
            for(int li=0;li<d.leds;li++){if(li)out<<",";out<<"{\"id\":"<<li<<",\"x\":"<<(li*24)<<",\"y\":"<<(di*34)<<",\"group\":"<<di<<",\"name\":\""<<esc(d.ledNames[li])<<"\"}";}
            out<<"]}";
        }
        out<<"],\"colors\":{";
        bool firstDev=true;
        for(size_t di=0;di<devices.size();di++){
            auto& d=devices[di];std::string id="msi:"+hex8(fnv(d.type));
            if(!firstDev)out<<",";firstDev=false;out<<"\""<<id<<"\":{";
            BSTR type=SysAllocStringLen(d.type.data(),(UINT)d.type.size());
            for(int li=0;li<d.leds;li++){
                if(li)out<<",";
                writeStage(stagePath,"before_get_led_color",(long long)li,utf8(d.type));
                DWORD r=0,g=0,b=0,ex=0;
                int cc=call_color(getColor,type,(DWORD)li,&r,&g,&b,&ex);
                writeStage(stagePath,"after_get_led_color",(long long)cc,utf8(d.type)+" #"+std::to_string(li));
                out<<"\""<<li<<"\":["<<(cc==0?std::min<DWORD>(255,r):0)<<","<<(cc==0?std::min<DWORD>(255,g):0)<<","<<(cc==0?std::min<DWORD>(255,b):0)<<"]";
            }
            SysFreeString(type);out<<"}";
        }
        out<<"},\"diagnostic\":{\"native_helper\":true,\"com_result\":"<<(long long)co<<",\"initialize_code\":"<<initCode<<",\"device_info_code\":"<<infoCode<<",\"device_info_exception\":"<<infoSeh<<",\"device_count\":"<<devices.size()<<",\"raw_device_types\":[";
        for(size_t i=0;i<devices.size();i++){if(i)out<<",";out<<"\""<<esc(devices[i].type)<<"\"";}
        out<<"],\"raw_led_counts\":[";
        for(size_t i=0;i<devices.size();i++){if(i)out<<",";out<<"\""<<devices[i].leds<<"\"";}
        out<<"]},\"error\":\""<<(infoCode==0&&infoSeh==0?"":"Echec MLAPI_GetDeviceInfo")<<"\"}";
        writeStage(stagePath,"response_written",(long long)devices.size());
        std::cout<<out.str()<<"\n"<<std::flush;
    }
    writeStage(stagePath,"stdin_closed",0);
    if(release)release();
    FreeLibrary(mod);if(SUCCEEDED(co))CoUninitialize();return 0;
}
