#include "kws_frontend.h"
#include "kws_frontend_data.h"
#include "../../vendor/micro_speech_baseline/signal/src/window.h"
#include "../../vendor/micro_speech_baseline/signal/src/fft_auto_scale.h"
#include "../../vendor/micro_speech_baseline/signal/src/energy.h"
#include "../../vendor/micro_speech_baseline/signal/src/filter_bank.h"
#include "../../vendor/micro_speech_baseline/signal/src/filter_bank_square_root.h"
#include "../../vendor/micro_speech_baseline/signal/src/filter_bank_spectral_subtraction.h"
#include "../../vendor/micro_speech_baseline/signal/src/pcan_argc_fixed.h"
#include "../../vendor/micro_speech_baseline/signal/src/filter_bank_log.h"

extern "C" void kws_rfft512(const int16_t*, int16_t*);
using namespace tflite::tflm_signal;

static int16_t windowed[480], fft_input[512];
static Complex<int16_t> spectrum[257];
static uint32_t energy[257], magnitude[40], reduced[40], noise_estimate[40];
static uint64_t bank[41];
static int16_t logged[40];
static const FilterbankConfig filter_config = {
    40, kws_pp_11, kws_pp_10, kws_pp_9, kws_pp_13, kws_pp_12, 64, 3
};
static const SpectralSubtractionConfig subtraction_config = {
    40, 409, 15975, 819, 983, 15401, 10, 14, false
};

extern "C" void kws_frontend_reset(void) {
    for (int i = 0; i < 40; ++i) noise_estimate[i] = 0;
}

extern "C" void kws_frontend_frame(const int16_t pcm[480], int8_t feature[40]) {
    ::tflm_signal::ApplyWindow(pcm, kws_pp_1, 480, 12, windowed);
    int scale = FftAutoScale(windowed, 480, fft_input);
    for (int i = 480; i < 512; ++i) fft_input[i] = 0;
    kws_rfft512(fft_input, (int16_t*)spectrum);
    for (int i = 0; i < 257; ++i) energy[i] = 0;
    SpectrumToEnergy(spectrum, 5, 241, energy);
    FilterbankAccumulateChannels(&filter_config, energy, bank);
    FilterbankSqrt(bank + 1, 40, scale, magnitude);
    FilterbankSpectralSubtraction(&subtraction_config, magnitude, reduced, noise_estimate);
    ApplyPcanAutoGainControlFixed(kws_pp_8, 6, noise_estimate, reduced, 40);
    FilterbankLog(reduced, 40, 64, 3, logged);
    for (int i = 0; i < 40; ++i) {
        int value = ((int)logged[i] * 256 + 333) / 666 - 128;
        if (value < -128) value = -128;
        if (value > 127) value = 127;
        feature[i] = (int8_t)value;
    }
}
