#pragma once

namespace tiny {

constexpr int kDefaultRetries = 3;
constexpr int kMaxRetries = 10;
constexpr int kBufferSize = 1024;

struct Config {
    int retries = kDefaultRetries;
    int buffer_size = kBufferSize;
};

const Config& GetConfig();

}  // namespace tiny
