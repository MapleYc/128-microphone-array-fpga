/* Fixed 512-point KISS FFT adapter. Upstream algorithms and license retained
 * under vendor/micro_speech_baseline/kissfft_v130; official TFLM patch applied
 * only to a build copy. No allocation/trigonometry is used by this runtime.
 */
#define FIXED_POINT 16
#include "../../work/kws_baseline_20261022/frontend/kissfft_patched/kiss_fft.c"
#include "../../work/kws_baseline_20261022/frontend/kissfft_patched/tools/kiss_fftr.c"
#include "kws_fft_tables.h"
#include <stddef.h>

static kiss_fft_cpx fft_temporary[256];
static struct kiss_fftr_state fft_state = {
    (kiss_fft_cfg)&kws_fft_substate, fft_temporary, (kiss_fft_cpx*)kws_fft_super
};

extern "C" void kws_rfft512(const int16_t* input, int16_t* output) {
    kiss_fftr(&fft_state, input, (kiss_fft_cpx*)output);
}
