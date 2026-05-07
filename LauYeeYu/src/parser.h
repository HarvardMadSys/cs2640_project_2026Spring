#include "config.h"
#include "libCacheSim/request.h"
#include <nlohmann/json.hpp>
#include <libCacheSim.h>

#include <fstream>
#include <string>
#include <unordered_map>
#include <functional> // For std::hash

using json = nlohmann::json;
double compute_intensity_transform(double raw_intensity);

/**
 * @brief A class to parse QWen files.
 *
 * The class will read the file and parse the requests into a std::vector of request_t*.
 */
class QWenTrace {
 public:
  QWenTrace(std::string file) : file_(file) {
    parse();
  }
  ~QWenTrace() {
    for (auto req : requests_) {
      free_request(req);
    }
  }
  const std::vector<request_t*>& get_requests() const {
    return requests_;
  }
 private:
  std::string file_;
  std::vector<request_t*> requests_;

  /**
    * @brief Parse the file and return a JSON object.
    *
    * @return nlohmann::json
   */
  std::vector<obj_id_t> parse_one_json(const std::string& input) {
    std::vector<obj_id_t> hash_ids;
    // Parse the JSON line
    json j = json::parse(input);
    // Check if hash_ids array exists
    if (j.contains("hash_ids")) {
        for (const auto& id : j["hash_ids"]) {
            hash_ids.push_back(id.get<int>());
        }
    }
    return hash_ids;
  }

  void parse() {
    std::ifstream infile(file_);
    std::string line;
    int count = 0;
    int request_count = 0;
    std::unordered_map<obj_id_t, request_t*> id_map; // map the id to the request for updating next access time
    std::unordered_map<obj_id_t, int> last_access_map; // map the id to the last access time
    while (std::getline(infile, line)) {
      std::vector<obj_id_t> original_hash_ids = parse_one_json(line);
          request_count++;
      
      // Translate original hash IDs to be position-aware by incorporating prefix hashes.
      // This logic mirrors the Python implementation for consistency.
      size_t cumulative_hash = 0;
      std::hash<size_t> hasher;

      for (int i = 0; i < original_hash_ids.size(); i++) {
          obj_id_t original_id = original_hash_ids[i];
          // Combine cumulative hash with the current original ID.
          // This mimics Python's hash((cumulative_hash, original_id)).
          // cumulative_hash = hasher(cumulative_hash ^ (hasher(original_id) + 0x9e3779b9 + (cumulative_hash << 6) + (cumulative_hash >> 2)));
          obj_id_t obj_id = original_id;

          request_t* req = new_request();
          req->obj_id = obj_id;

          // req features:
          // feature[0]: position index (i + 1)
          // feature[1]: total number of positions (original_hash_ids.size()) for the first position, else 0
          // feature[2]: global request count
          // feature[3]: interval since last access, -1 if first access
          req->n_features = 4;
          req->next_access_vtime = INT64_MAX; // default value
          req->features[0] = i + 1;
          req->features[2] = request_count;
          req->cost = compute_intensity_transform((double)(i + 1));
          if (i == 0) {
            req->features[1] = (int)original_hash_ids.size();
          } else {
            req->features[1] = 0;
          }
          if (last_access_map.find(obj_id) != last_access_map.end()) {
            req->features[3] = request_count - last_access_map[obj_id];
          } else {
            req->features[3] = -1;
          }
          last_access_map[obj_id] = request_count;
          req->clock_time = count++;
          req->valid = true;
          req->op = OP_READ;
          req->obj_size = 1;
          requests_.push_back(req);
          if (id_map.find(obj_id) != id_map.end()) {
              id_map[obj_id]->next_access_vtime = req->clock_time;
          }
          id_map[obj_id] = req;
      }
    }
  }
};
