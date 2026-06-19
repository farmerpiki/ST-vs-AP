#include "matrix.hpp"

#include <stdexcept>

namespace tiny {

Matrix::Matrix(std::size_t rows, std::size_t cols)
    : rows_(rows), cols_(cols), data_(rows * cols, 0.0) {
    if (rows == 0 || cols == 0) {
        throw std::invalid_argument("Matrix dimensions must be non-zero");
    }
}

// Sum of every element. Naive flat loop - touches every byte once, cache
// friendly because the data is contiguous.
double Matrix::SumAll() const {
    double s = 0.0;
    for (std::size_t i = 0; i < data_.size(); ++i) {
        s += data_[i];
    }
    return s;
}

// Per-row sum: walk row by row. Each row is contiguous in memory so the
// access pattern is good. This is the obvious "right" way to do it.
std::vector<double> Matrix::SumPerRow() const {
    std::vector<double> result(rows_, 0.0);
    for (std::size_t r = 0; r < rows_; ++r) {
        double s = 0.0;
        for (std::size_t c = 0; c < cols_; ++c) {
            s += at(r, c);
        }
        result[r] = s;
    }
    return result;
}

// Per-column sum: walk column by column. Each access is (data_[0 * cols + c],
// data_[1 * cols + c], ...). The stride between accesses is `cols` doubles,
// which is much larger than a typical cache line (64 bytes / 8 doubles).
// On a 1024x1024 matrix this strides by 8KB per access, blowing the L1
// cache on every load. This is the slow path callers complain about.
std::vector<double> Matrix::SumPerColumn() const {
    std::vector<double> result(cols_, 0.0);
    for (std::size_t c = 0; c < cols_; ++c) {
        double s = 0.0;
        for (std::size_t r = 0; r < rows_; ++r) {
            s += at(r, c);
        }
        result[c] = s;
    }
    return result;
}

// In-place transpose of a square matrix. We swap at(i, j) with at(j, i)
// for the upper triangle. The row-major layout means at(i, j) reads
// `cols_` elements ahead of the previous read - on a wide matrix that
// thrashes the cache. Faster implementations do a block transpose.
void TransposeSquare(Matrix& m) {
    if (m.rows() != m.cols()) {
        throw std::invalid_argument("TransposeSquare requires a square matrix");
    }
    const std::size_t n = m.rows();
    for (std::size_t i = 0; i < n; ++i) {
        for (std::size_t j = i + 1; j < n; ++j) {
            const double tmp = m.at(i, j);
            m.at(i, j) = m.at(j, i);
            m.at(j, i) = tmp;
        }
    }
}

}  // namespace tiny
