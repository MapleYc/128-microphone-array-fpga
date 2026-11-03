// Streaming wrapper for the unchanged MicArray 48 HOST postprocessing classes.
// API accepts exactly 512 samples per full block; at EOF only, pass a final
// block shorter than 512 and mark final=1. No padding or overlap flush.
#include "media/bandpassfilter.h"
#include "media/logmmseprocessor.h"
#include "media/automaticgaincontrol.h"
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QVector>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>

namespace {
constexpr int kBlock = 512;
thread_local std::string last_error;
[[noreturn]] void fail(const char* text) { throw std::runtime_error(text); }
QJsonObject object(const QJsonObject &x, const char *key) {
    const auto v=x.value(key); if(!v.isObject()) fail("missing config object"); return v.toObject();
}
double number(const QJsonObject &x,const char *key,double low,double high) {
    const auto v=x.value(key); const double n=v.toDouble(NAN);
    if(!v.isDouble()||!std::isfinite(n)||n<low||n>high) fail("invalid config number"); return n;
}
int integer(const QJsonObject &x,const char *key,int low,int high) {
    const double n=number(x,key,low,high);if(std::floor(n)!=n)fail("invalid integer");return int(n);
}
bool boolean(const QJsonObject &x,const char *key) {
    const auto v=x.value(key);if(!v.isBool())fail("invalid config boolean");return v.toBool();
}
struct State {
    MicArray::BandPassFilter bp;
    MicArray::LogMmseProcessor log;
    MicArray::AutomaticGainControl agc;
    bool bp_on=false,log_on=false,agc_on=false,closed=false;
    long long samples=0,blocks=0;
    explicit State(const char* config_json) {
        if(!config_json)fail("null config");QJsonParseError err;
        const auto doc=QJsonDocument::fromJson(QByteArray(config_json),&err);
        if(err.error!=QJsonParseError::NoError||!doc.isObject())fail("invalid JSON");
        const QJsonObject cfg=doc.object();integer(cfg,"schema_version",1,1);const int fs=integer(cfg,"sample_rate_hz",48000,48000);
        const auto b=object(cfg,"bandpass");bp_on=boolean(b,"enabled");
        const double lo=number(b,"low_hz",0,fs/2.0), hi=number(b,"high_hz",0,fs/2.0);
        if(bp_on&&!bp.configure(fs,lo,hi))fail("bandpass configure failed");
        const auto l=object(cfg,"logmmse");MicArray::LogMmseSettings ls;
        ls.enabled=boolean(l,"enabled");log_on=ls.enabled;ls.fftSize=integer(l,"fft_size",256,2048);
        ls.overlapRatio=number(l,"overlap_ratio",0,1);ls.priorSnrSmoothing=number(l,"prior_snr_smoothing",0,1);
        ls.noisePowerSmoothing=number(l,"noise_power_smoothing",0,1);ls.minimumGainDb=number(l,"minimum_gain_db",-80,0);
        if(log_on&&!log.configure(fs,ls))fail("logmmse configure failed");
        const auto a=object(cfg,"agc");MicArray::AutomaticGainControlSettings as;
        as.enabled=boolean(a,"enabled");agc_on=as.enabled;as.targetLevelDbfs=number(a,"target_level_dbfs",-60,0);
        as.maximumGainDb=number(a,"maximum_gain_db",0,60);as.maximumAttenuationDb=number(a,"maximum_attenuation_db",-60,0);
        as.attackMilliseconds=number(a,"attack_ms",1,1000);as.releaseMilliseconds=number(a,"release_ms",1,5000);
        as.limiterLevelDbfs=number(a,"limiter_level_dbfs",-20,0);
        if(agc_on&&!agc.configure(fs,as))fail("agc configure failed");
        const auto o=cfg.value("post_processing_order");if(!o.isArray())fail("missing order");
        const auto order=o.toArray();if(order.size()!=3||order[0].toString()!="bandpass"||order[1].toString()!="logmmse"||order[2].toString()!="agc")fail("unsupported postprocessing order");
    }
    void run(const float* input,int n,float* band,float* log_out,float* final,bool last) {
        if(closed)fail("stream already finished");
        if(n<0||n>kBlock||(!last&&n!=kBlock)||(last&&n>=kBlock))fail("invalid block or finish length");
        if(n&&!input)fail("null input");if(n&&(!band||!log_out||!final))fail("null output");
        QVector<float> x;x.reserve(n);for(int i=0;i<n;i++){if(!std::isfinite(input[i]))fail("nonfinite input");x.append(input[i]);}
        if(n) {
            if(bp_on)x=bp.process(x);if(x.size()!=n)fail("bandpass changed length");std::copy(x.cbegin(),x.cend(),band);
            if(log_on)x=log.process(x);if(x.size()!=n)fail("logmmse changed length");std::copy(x.cbegin(),x.cend(),log_out);
            if(agc_on)x=agc.process(x);if(x.size()!=n)fail("agc changed length");std::copy(x.cbegin(),x.cend(),final);
            for(float value:x)if(!std::isfinite(value))fail("nonfinite output");samples+=n;blocks++;
        }
        if(last)closed=true;
    }
};
}
extern "C" {
__declspec(dllexport) const char* stream_post_error(){return last_error.c_str();}
__declspec(dllexport) void* stream_post_create(const char* cfg){try{last_error.clear();return new State(cfg);}catch(const std::exception&e){last_error=e.what();return nullptr;}}
__declspec(dllexport) int stream_post_process(void* h,const float* in,int n,float* band,float* log_out,float* final,int last){try{last_error.clear();if(!h)fail("null state");static_cast<State*>(h)->run(in,n,band,log_out,final,last!=0);return 0;}catch(const std::exception&e){last_error=e.what();return -1;}}
__declspec(dllexport) void stream_post_destroy(void* h){delete static_cast<State*>(h);}
}
