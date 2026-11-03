#include "../base/firmware/include/meeting_spatial.h"
#include "../base/firmware/include/meeting_pair_geometry.h"
#include "../base/firmware/include/array128_geometry.h"
static uint32_t root64(uint64_t v)
{
    uint64_t r=0,bit=UINT64_C(1)<<62;
    while(bit>v)bit>>=2;
    while(bit) {if(v>=r+bit){v-=r+bit;r=(r>>1)+bit;}else r>>=1;bit>>=2;}
    return (uint32_t)r;
}
static const int32_t atan_q8[16]={11520,6801,3593,1824,916,458,229,115,57,29,14,7,4,2,1,0};
static int32_t angle(int32_t y,int32_t x)
{
    unsigned i;int32_t z=0;
    for(i=0;i<16;++i){int32_t old=x;if(y>0){x+=y>>i;y-=old>>i;z+=atan_q8[i];}
        else if(y<0){x-=y>>i;y+=old>>i;z-=atan_q8[i];}}
    return z;
}
static void sincos_q15(int32_t z,int32_t *s,int32_t *c)
{
    unsigned i;int32_t x=19898,y=0;
    for(i=0;i<16;++i){int32_t old=x;if(z>=0){x-=y>>i;y+=old>>i;z-=atan_q8[i];}
        else{x+=y>>i;y-=old>>i;z+=atan_q8[i];}}
    *s=y;*c=x;
}
__attribute__((noinline,noclone)) int meeting_spatial_fit_floor(const meeting_pair_result p[64],uint64_t frame,meeting_spatial_result *r,uint32_t floor)
{
    unsigned i,pass,n=0;uint16_t q[64];uint8_t use[64];
    int32_t ux=0,uy=0;uint32_t quality=0,residual=0;
    if(!p || !r)return -1;
    for(i=0;i<64;++i){uint32_t den=root64((uint64_t)p[i].energy_a*p[i].energy_b);
        uint64_t score=den ? ((uint64_t)p[i].correlation*32768u)/den : 0;
        q[i]=(uint16_t)(score>32767u ? 32767u : score);
        use[i]=(uint8_t)(q[i]>=18022u && p[i].energy_a>=floor && p[i].energy_b>=floor && p[i].lag>=-56 && p[i].lag<=56);}
    for(pass=0;pass<2;++pass){
        int64_t a=0,b=0,c=0,sx=0,sy=0,det,nx,ny;n=0;quality=0;
        for(i=0;i<64;++i)if(use[i]){int32_t x=meeting_pair_mm[i][0],y=meeting_pair_mm[i][1];
            a+=(int64_t)x*x;b+=(int64_t)x*y;c+=(int64_t)y*y;
            sx+=(int64_t)x*p[i].lag;sy+=(int64_t)y*p[i].lag;++n;quality+=q[i];}
        det=a*c-b*b;
        if(n<32 || a==0 || c==0 || det<(a*c)/20)return 0;
        nx=sx*c-sy*b;ny=sy*a-sx*b;
        ux=(int32_t)(((nx*32768)/det)*343/48);
        uy=(int32_t)(((ny*32768)/det)*343/48);
        if((int64_t)ux*ux+(int64_t)uy*uy>INT64_C(1073741824))return 0;
        residual=0;
        for(i=0;i<64;++i)if(use[i]){
            int64_t projection=(int64_t)meeting_pair_mm[i][0]*ux+(int64_t)meeting_pair_mm[i][1]*uy;
            int32_t predicted=(int32_t)(projection*48/(343*32));
            int32_t delta=predicted-p[i].lag*1024;if(delta<0)delta=-delta;
            if(pass==0 && delta>1536)use[i]=0;
            if((uint32_t)delta>residual)residual=(uint32_t)delta;
        }
    }
    if(residual>2048u)return 0;
    r->ux_q15=ux;r->uy_q15=uy;r->accepted_pairs=n;r->residual_q10=residual;
    r->direction.frame=frame;
    r->direction.azimuth_x256=angle(ux,(int32_t)root64(UINT64_C(1073741824)-(int64_t)ux*ux-(int64_t)uy*uy));
    r->direction.elevation_x256=angle(uy,(int32_t)root64(UINT64_C(1073741824)-(int64_t)uy*uy));
    r->direction.quality_q15=(uint16_t)(quality/64u);
    if(r->direction.azimuth_x256< -80*256 || r->direction.azimuth_x256>80*256 ||
       r->direction.elevation_x256< -40*256 || r->direction.elevation_x256>40*256)return 0;
    return 1;
}
int candidate_unused_delays(int32_t az,int32_t el,uint16_t delays[128])
{
    int32_t sa,ca,se,ce,ux,uy;unsigned i;
    if(!delays || az< -80*256 || az>80*256 || el< -40*256 || el>40*256)return -1;
    sincos_q15(az,&sa,&ca);sincos_q15(el,&se,&ce);(void)ca;
    ux=(int32_t)(((int64_t)sa*ce)/32768);uy=se;
    for(i=0;i<128;++i){
        int64_t dot=(int64_t)array128_positions_um[i].x_um*ux+(int64_t)array128_positions_um[i].y_um*uy;
        int64_t delay=16384+dot*12288000/INT64_C(11239424000000);
        if(delay<0 || delay>32512)return -1;
        delays[i]=(uint16_t)delay;
    }
    return 0;
}
