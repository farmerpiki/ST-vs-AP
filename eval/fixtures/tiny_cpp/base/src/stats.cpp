#include "stats.hpp"

#include <algorithm>
#include <cstddef>
#include <vector>

namespace tiny {

// Returns the median of the values in `samples`. For an even-length input
// the median is the mean of the two middle values after sorting. For an
// empty input the function returns 0.0.
double Median(std::vector<double> samples) {
    if (samples.empty()) return 0.0;
    std::sort(samples.begin(), samples.end());
    const std::size_t n = samples.size();
    if (n % 2 == 1) {
        return samples[n / 2];
    }
    // BUG: even-length returns the lower middle value instead of the mean.
    // For [1, 2, 3, 4] we return 2.0 instead of 2.5.
    return samples[n / 2 - 1];
}

double Mean(const std::vector<double>& samples) {
    if (samples.empty()) return 0.0;
    double total = 0.0;
    for (double v : samples) total += v;
    return total / static_cast<double>(samples.size());
}

std::vector<double> Sorted(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    return values;
}

std::vector<double> MinMaxBuckets(const std::vector<double>& values,
                                  std::size_t num_buckets) {
    std::vector<double> out(2 * num_buckets, 0.0);
    if (values.empty() || num_buckets == 0) return out;
    const std::size_t n = values.size();
    const std::size_t per = n / num_buckets;
    const std::size_t extra = n % num_buckets;
    std::size_t cursor = 0;
    for (std::size_t b = 0; b < num_buckets; ++b) {
        const std::size_t size = per + (b < extra ? 1 : 0);
        const std::size_t lo = cursor;
        const std::size_t hi = cursor + size;
        if (lo < hi) {
            double mn = values[lo];
            double mx = values[lo];
            for (std::size_t i = lo; i < hi; ++i) {
                if (values[i] < mn) mn = values[i];
                if (values[i] > mx) mx = values[i];
            }
            out[2 * b]     = mn;
            out[2 * b + 1] = mx;
        }
        cursor = hi;
    }
    return out;
}

}  // namespace tiny
