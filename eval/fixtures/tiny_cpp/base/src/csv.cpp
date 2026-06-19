#include "csv.hpp"

namespace tiny {

std::vector<std::string> SplitCsvRow(std::string_view row) {
    std::vector<std::string> out;
    std::string cur;
    bool in_quotes = false;
    for (std::size_t i = 0; i < row.size(); ++i) {
        const char c = row[i];
        if (in_quotes) {
            if (c == '"') {
                // Look ahead for an escaped double-quote.
                if (i + 1 < row.size() && row[i + 1] == '"') {
                    cur.push_back('"');
                    ++i;
                } else {
                    in_quotes = false;
                }
            } else {
                cur.push_back(c);
            }
        } else {
            if (c == ',') {
                out.push_back(std::move(cur));
                cur.clear();
            } else if (c == '"' && cur.empty()) {
                in_quotes = true;
            } else {
                cur.push_back(c);
            }
        }
    }
    out.push_back(std::move(cur));
    return out;
}

std::string JoinCsvRow(const std::vector<std::string>& fields) {
    if (fields.empty()) return std::string();
    std::string out;
    bool first = true;
    for (const std::string& f : fields) {
        if (!first) out.push_back(',');
        first = false;
        const bool needs_quotes = (f.find_first_of(",\"\n") != std::string::npos);
        if (!needs_quotes) {
            out.append(f);
        } else {
            out.push_back('"');
            for (char c : f) {
                if (c == '"') out.push_back('"');
                out.push_back(c);
            }
            out.push_back('"');
        }
    }
    return out;
}

}  // namespace tiny
