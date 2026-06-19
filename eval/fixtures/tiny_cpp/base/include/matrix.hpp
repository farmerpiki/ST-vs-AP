#pragma once

#include <cstddef>
#include <vector>

namespace tiny {

// A simple dense matrix stored in row-major order.
//
// Row-major means element (r, c) lives at index (r * cols + c). Iterating
// row by row touches contiguous memory and is friendly to CPU caches.
class Matrix {
public:
    Matrix(std::size_t rows, std::size_t cols);

    std::size_t rows() const { return rows_; }
    std::size_t cols() const { return cols_; }
    std::size_t size() const { return data_.size(); }

    // Element access.
    double&       at(std::size_t r, std::size_t c)       { return data_[r * cols_ + c]; }
    const double& at(std::size_t r, std::size_t c) const { return data_[r * cols_ + c]; }

    // Computes the sum of all elements.
    double SumAll() const;

    // Computes the per-row sum of all elements. The result vector has
    // length rows(). result[r] = sum of row r.
    std::vector<double> SumPerRow() const;

    // Computes the per-column sum of all elements. The result vector has
    // length cols(). result[c] = sum of column c.
    std::vector<double> SumPerColumn() const;

private:
    std::size_t rows_;
    std::size_t cols_;
    std::vector<double> data_;
};

// Performs an in-place transpose of a square matrix.
void TransposeSquare(Matrix& m);

}  // namespace tiny
