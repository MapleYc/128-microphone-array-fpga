#include <cstdint>
#include <cmath>
// Match the original per-microphone 4-tap double arithmetic, followed by
// clipping and float32 rounding before the sensor-axis double mean.
extern "C" __declspec(dllexport) void frame_kernel(
 const float* ring, int cap, const std::int64_t* channels,
 const std::int64_t* delay, const double* c, int count,
 std::int64_t start, float* out) {
 for (int t=0;t<1024;++t) {
  double sum=0.0;
  for(int m=0;m<count;++m) {
   double v=0.0;
   for(int k=0;k<4;++k) {
    std::int64_t ix=start+t-delay[m]-k+1;
    std::int64_t slot=ix%cap; if(slot<0)slot+=cap;
    double x=static_cast<double>(ring[channels[m]*cap+slot]);
    double z=(c[4*m+k]*x)*(ix>=0 ? 1.0:0.0);
    v=v+z;
   }
   if(v < -1.0)v=-1.0; if(v > 1.0)v=1.0;
   float f=static_cast<float>(v);
   sum=sum+static_cast<double>(f);
  }
  out[t]=(start+t<60) ? 0.0f : static_cast<float>(sum/static_cast<double>(count));
 }
}
