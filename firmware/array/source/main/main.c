#define MEETING_DIAGNOSTIC_DEFAULT_FLOOR 4
#include "fitter.c"
/* Opt-in bring-up overlay. The ordinary application entry and default firmware
 * remain unchanged. A verified generated fitter provides the explicit energy
 * floor argument; no automatic threshold/gain adjustment occurs here.
 */
#include "../base/firmware/include/meeting_spatial_io.h"
#include "../base/firmware/include/meeting_transport.h"
int meeting_diagnostic_doa_read(meeting_spatial_result *result);
int meeting_diagnostic_command(void);
#define meeting_doa_read meeting_diagnostic_doa_read
#define meeting_transport_command meeting_diagnostic_command
#include "../entry/meeting_av_main.c"
#undef meeting_doa_read
#undef meeting_transport_command

#ifndef MEETING_DIAGNOSTIC_DEFAULT_FLOOR
#define MEETING_DIAGNOSTIC_DEFAULT_FLOOR 64
#endif
static uint32_t diagnostic_floor=MEETING_DIAGNOSTIC_DEFAULT_FLOOR,diagnostic_shift=8;
static meeting_pair_result diagnostic_snapshot[64];
static uint64_t diagnostic_frame;
static uint32_t diagnostic_epoch;
static unsigned diagnostic_requested,diagnostic_pending,diagnostic_index;
static unsigned diagnostic_config_pending=1;
static int diagnostic_fit;

static void diagnostic_service(void)
{
    uint32_t header[8];uint8_t payload[288];unsigned count,i,j,first;
    if(!meeting_transport_idle() || rd(UINT32_C(0x20006110))>=80u ||
       rd(UINT32_C(0x20006400))>=80u)return;
    if(!diagnostic_config_pending && !diagnostic_pending)return;
    first=diagnostic_config_pending?0:diagnostic_index;
    count=diagnostic_config_pending?0:16;
    header[0]=1;header[1]=diagnostic_floor;header[2]=diagnostic_shift;
    header[3]=first;header[4]=count;header[5]=(uint32_t)diagnostic_fit;
    header[6]=diagnostic_config_pending?1u:2u;header[7]=0;
    for(i=0;i<8;++i)for(j=0;j<4;++j)payload[i*4+j]=(uint8_t)(header[i]>>(8*j));
    for(i=0;i<count;++i){
        const meeting_pair_result *p=&diagnostic_snapshot[first+i];
        uint32_t words[4]={(uint32_t)p->lag,p->correlation,p->energy_a,p->energy_b};
        for(unsigned k=0;k<4;++k)for(j=0;j<4;++j)payload[32+i*16+k*4+j]=(uint8_t)(words[k]>>(8*j));
    }
    if(meeting_transport_packet(5, count?diagnostic_frame:meeting_source_now(),
        count?diagnostic_epoch:app.controller.epoch,payload,32+count*16)==1){
        if(diagnostic_config_pending)diagnostic_config_pending=0;
        else{diagnostic_index+=count;if(diagnostic_index==64)diagnostic_pending=0;}
    }
}

int meeting_diagnostic_doa_read(meeting_spatial_result *result)
{
    meeting_pair_result pairs[64];meeting_spatial_result fresh;uint64_t frame;unsigned i;
    if(!result)return -1;
    if((rd(UINT32_C(0x20006208))&7u)!=2u)return 0;
    frame=((uint64_t)rd(UINT32_C(0x20006224))<<32)|rd(UINT32_C(0x20006220));
    for(i=0;i<64;++i){
        *(volatile uint32_t *)(uintptr_t)UINT32_C(0x2000620c)=i;
        pairs[i].lag=(int32_t)rd(UINT32_C(0x20006210));
        pairs[i].correlation=rd(UINT32_C(0x20006214));
        pairs[i].energy_a=rd(UINT32_C(0x20006218));pairs[i].energy_b=rd(UINT32_C(0x2000621c));
    }
    int fit=meeting_spatial_fit_floor(pairs,frame,&fresh,diagnostic_floor);
    /* A rejected fit must not overwrite the telemetry's last accepted result. */
    if(fit==1)*result=fresh;
    if(diagnostic_requested && !diagnostic_pending){
        for(i=0;i<64;++i)diagnostic_snapshot[i]=pairs[i];
        diagnostic_frame=frame;diagnostic_epoch=app.controller.epoch;
        diagnostic_fit=fit;diagnostic_index=0;diagnostic_pending=1;diagnostic_requested=0;
    }
    return fit;
}

int meeting_diagnostic_command(void)
{
    diagnostic_service();int command=meeting_transport_command();
    if(command=='p'){diagnostic_requested=1;return -1;}
    if(command=='H' || command=='M' || command=='L'){
        diagnostic_floor=command=='H'?64u:command=='M'?16u:4u;
    }else if(command=='8' || command=='6' || command=='4'){
        diagnostic_shift=(uint32_t)(command-'0');
        if(array128_mono_start(0,0,diagnostic_shift)!=0)return -1;
    }else return command;
    /* Discard old diagnostic chunks and keyword/direction history whenever an
     * experiment changes its scaling or gate. Base main handles 'u' reset. */
    diagnostic_requested=0;diagnostic_pending=0;diagnostic_index=0;
    diagnostic_config_pending=1;direction=(meeting_spatial_result){0};
    return 'u';
}
