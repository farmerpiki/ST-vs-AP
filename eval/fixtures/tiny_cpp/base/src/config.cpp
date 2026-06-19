#include "config.hpp"

namespace tiny {

const Config& GetConfig() {
    static const Config kConfig = [] {
        Config c;
        c.retries = kDefaultRetries;
        c.buffer_size = kBufferSize;
        return c;
    }();
    return kConfig;
}

}  // namespace tiny
