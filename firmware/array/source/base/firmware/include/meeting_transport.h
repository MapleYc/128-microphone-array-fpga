#ifndef MEETING_TRANSPORT_H
#define MEETING_TRANSPORT_H
#include <stdint.h>
enum {MEETING_PACKET_STATUS=1,MEETING_PACKET_RAW=2,MEETING_PACKET_BEAM=3,MEETING_PACKET_CAMERA=4,
      MEETING_PACKET_CAMERA_SIZED=8};
void meeting_transport_service(void);
int meeting_transport_packet(unsigned type,uint64_t frame,uint32_t epoch,const uint8_t *data,unsigned bytes);
int meeting_transport_idle(void);
int meeting_transport_command(void);
uint32_t meeting_transport_drops(void);
#endif
