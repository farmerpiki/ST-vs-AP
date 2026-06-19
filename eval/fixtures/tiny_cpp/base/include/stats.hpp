#pragma once

#include <cstddef>
#include <vector>

namespace tiny {

// Returns the median of the values in `samples`. Does not sort the input
// in place. For an even-length input the median is the mean of the two
// middle values after sorting. For an empty input the function returns 0.0.
double Median(std::vector<double> samples);

// Returns the arithmetic mean of the values in `samples`. Returns 0.0
// for an empty input.
double Mean(const std::vector<double>& samples);

// Returns a vector of length n containing the values sorted in ascending
// order. The input vector is not modified.
std::vector<double> Sorted(std::vector<double> values);

// Splits `values` into `num_buckets` buckets of (approximately) equal
// size and returns a vector with the min/max of each bucket. The result
// vector has length 2*num_buckets, with result[2*i] = min of bucket i
// and result[2*i+1] = max of bucket i. For an empty input the result is
// empty. For fewer values than buckets some buckets will be empty and
// their min/max will be set to 0.0.
std::vector<double> MinMaxBuckets(const std::vector<double>& values,
                                  std::size_t num_buckets);

}  // namespace tiny
