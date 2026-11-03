#include "kws_cooperative.h"
#include "kws_model_data.h"

static int32_t requantize(int32_t value, int32_t multiplier, int shift) {
    int left = shift > 0 ? shift : 0;
    int right = shift < 0 ? -shift : 0;
    int32_t scaled = (int32_t)((uint32_t)value << left);
    int64_t product = (int64_t)scaled * multiplier;
    int64_t nudged = product + (product >= 0 ? ((int64_t)1 << 30) : 1 - ((int64_t)1 << 30));
    int32_t high = nudged >= 0 ? (int32_t)((uint64_t)nudged >> 31)
                               : -(int32_t)((uint64_t)(-nudged) >> 31);
    uint32_t mask = ((uint32_t)1 << right) - 1;
    return (high >> right) + (((uint32_t)high & mask) > ((mask >> 1) + (high < 0)));
}

static int8_t clip(int32_t value) {
    if (value < -128) return -128;
    if (value > 127) return 127;
    return (int8_t)value;
}

static uint32_t divide_u32(uint32_t numerator, uint32_t denominator) {
    uint32_t quotient = 0, remainder = 0;
    for (int bit = 31; bit >= 0; --bit) {
        remainder = (remainder << 1) | ((numerator >> bit) & 1u);
        if (remainder >= denominator) {
            remainder -= denominator;
            quotient |= (uint32_t)1 << bit;
        }
    }
    return quotient;
}

void kws_cooperative_init(KwsCooperative* state) {
    state->stage = KWS_IDLE;
    state->row_or_class = 0;
}

int kws_cooperative_begin(KwsCooperative* state, const int8_t* features) {
    if (state->stage != KWS_IDLE && state->stage != KWS_DONE) return 0;
    for (int i = 0; i < KWS_FEATURE_BYTES; ++i) state->features[i] = features[i];
    state->row_or_class = 0;
    state->stage = KWS_CONV;
    return 1;
}

int kws_cooperative_step(KwsCooperative* state) {
    if (state->stage == KWS_DONE) return 1;
    if (state->stage == KWS_CONV) {
        int oy = (int)state->row_or_class;
        for (int ox = 0; ox < 20; ++ox) {
            /* Reuse each feature for all eight output channels. Each channel
             * retains its original ky/kx accumulation order and requantizer. */
            int32_t a0=kws_conv_bias[0],a1=kws_conv_bias[1],a2=kws_conv_bias[2],a3=kws_conv_bias[3];
            int32_t a4=kws_conv_bias[4],a5=kws_conv_bias[5],a6=kws_conv_bias[6],a7=kws_conv_bias[7];
            for (int ky = 0; ky < 10; ++ky) {
                int iy = oy * 2 + ky - 4;
                if (iy < 0 || iy >= 49) continue;
                for (int kx = 0; kx < 8; ++kx) {
                    int ix = ox * 2 + kx - 3;
                    if (ix < 0 || ix >= 40) continue;
                    int v=(int)state->features[iy*40+ix]+128;
                    const int8_t *w=&kws_conv_weights[(ky*8+kx)*8];
                    a0+=v*w[0];a1+=v*w[1];a2+=v*w[2];a3+=v*w[3];
                    a4+=v*w[4];a5+=v*w[5];a6+=v*w[6];a7+=v*w[7];
                }
            }
            const int32_t sums[8]={a0,a1,a2,a3,a4,a5,a6,a7};
            for(int ch=0;ch<8;++ch)
                state->scratch[(oy*20+ox)*8+ch]=clip(requantize(sums[ch],kws_conv_multiplier[ch],kws_conv_shift[ch])-128);
        }
        if (++state->row_or_class == 25) {
            state->stage = KWS_FC;
            state->row_or_class = 0;
        }
        return 0;
    }
    if (state->stage == KWS_FC) {
        int cls = (int)state->row_or_class;
        int32_t acc = kws_fc_bias[cls];
        for (int i = 0; i < 4000; ++i)
            acc += ((int)state->scratch[i] + 128) * kws_fc_weights[cls * 4000 + i];
        state->logits[cls] = clip(requantize(acc, KWS_FC_MULTIPLIER, KWS_FC_SHIFT) + 14);
        if (++state->row_or_class == 4) state->stage = KWS_SOFTMAX;
        return 0;
    }
    if (state->stage == KWS_SOFTMAX) {
        int maximum = -128;
        uint32_t values[4], denominator = 0;
        for (int i = 0; i < 4; ++i)
            if (state->logits[i] > maximum) maximum = state->logits[i];
        for (int i = 0; i < 4; ++i) {
            values[i] = kws_softmax_lut[maximum - (int)state->logits[i]];
            denominator += values[i];
        }
        for (int i = 0; i < 4; ++i) {
            uint32_t value = divide_u32((values[i] << 8) + (denominator >> 1), denominator);
            if (value > 255) value = 255;
            state->output[i] = (int8_t)((int)value - 128);
        }
        state->stage = KWS_DONE;
        return 1;
    }
    return -1;
}
