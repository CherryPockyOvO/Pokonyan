#include <iostream>
#include <vector>
#include <string>
#include <thread>
#include <mutex>
#include <atomic>
#include <chrono>
#include <csignal>
#include <algorithm>

#include <windows.h>
#include <winhttp.h>
#pragma comment(lib, "winhttp.lib")

#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include "whisper.h"

static std::atomic<bool> g_running{true};

void signal_handler(int sig) {
    if (sig == SIGINT || sig == SIGTERM) {
        g_running = false;
    }
}

// Audio Ring Buffer
class AudioBuffer {
private:
    std::vector<float> buffer;
    std::mutex mtx;
    size_t max_samples;

public:
    AudioBuffer(size_t max_seconds = 30, uint32_t sample_rate = 16000) {
        max_samples = max_seconds * sample_rate;
        buffer.reserve(max_samples);
    }

    void append(const float* data, size_t count) {
        std::lock_guard<std::mutex> lock(mtx);
        buffer.insert(buffer.end(), data, data + count);
        if (buffer.size() > max_samples) {
            size_t overflow = buffer.size() - max_samples;
            buffer.erase(buffer.begin(), buffer.begin() + overflow);
        }
    }

    std::vector<float> get_recent_samples(size_t seconds, uint32_t sample_rate = 16000) {
        std::lock_guard<std::mutex> lock(mtx);
        size_t req_samples = seconds * sample_rate;
        if (buffer.empty()) return {};

        if (buffer.size() <= req_samples) {
            return buffer;
        } else {
            return std::vector<float>(buffer.end() - req_samples, buffer.end());
        }
    }

    void clear() {
        std::lock_guard<std::mutex> lock(mtx);
        buffer.clear();
    }
};

void data_callback(ma_device* pDevice, void* pOutput, const void* pInput, ma_uint32 frameCount) {
    AudioBuffer* audio_buf = (AudioBuffer*)pDevice->pUserData;
    if (pInput && audio_buf && frameCount > 0) {
        audio_buf->append((const float*)pInput, frameCount);
    }
    (void)pOutput;
}

// WinHTTP POST Helper function
bool send_text_to_pi(const std::string& pi_host, int port, const std::string& text) {
    std::wstring wstr_host(pi_host.begin(), pi_host.end());

    HINTERNET hSession = WinHttpOpen(L"PokonyanCppAudioClient/1.0",
                                    WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                    WINHTTP_NO_PROXY_NAME,
                                    WINHTTP_NO_PROXY_BYPASS, 0);
    if (!hSession) return false;

    HINTERNET hConnect = WinHttpConnect(hSession, wstr_host.c_str(), (INTERNET_PORT)port, 0);
    if (!hConnect) {
        WinHttpCloseHandle(hSession);
        return false;
    }

    HINTERNET hRequest = WinHttpOpenRequest(hConnect, L"POST", L"/transcribe_text",
                                            NULL, WINHTTP_NO_REFERER,
                                            WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
    if (!hRequest) {
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return false;
    }

    std::string escaped_text = "";
    for (char c : text) {
        if (c == '"') escaped_text += "\\\"";
        else if (c == '\\') escaped_text += "\\\\";
        else escaped_text += c;
    }
    std::string json_body = "{\"text\":\"" + escaped_text + "\"}";

    LPCWSTR headers = L"Content-Type: application/json\r\n";
    BOOL bResults = WinHttpSendRequest(hRequest, headers, -1L,
                                       (LPVOID)json_body.c_str(), (DWORD)json_body.size(),
                                       (DWORD)json_body.size(), 0);

    if (bResults) {
        bResults = WinHttpReceiveResponse(hRequest, NULL);
    }

    WinHttpCloseHandle(hRequest);
    WinHttpCloseHandle(hConnect);
    WinHttpCloseHandle(hSession);

    return bResults == TRUE;
}

void print_usage(const char* exe_name) {
    std::cout << "Usage: " << exe_name << " [options]\n"
              << "Options:\n"
              << "  --pi-host <ip/name>     Raspberry Pi IP address (default: 100.80.242.72)\n"
              << "  --port <port>           Raspberry Pi web server port (default: 8080)\n"
              << "  -m, --model <path>      Path to GGML whisper model file (default: models/ggml-base.bin)\n"
              << "  -l, --language <lang>   Language code (default: en)\n"
              << "  -h, --help              Show help message\n";
}

int main(int argc, char** argv) {
    std::string pi_host = "100.80.242.72";
    int pi_port = 8080;
    std::string model_path = "models/ggml-base.bin";
    std::string language = "en";

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--pi-host" && i + 1 < argc) {
            pi_host = argv[++i];
        } else if (arg == "--port" && i + 1 < argc) {
            pi_port = std::stoi(argv[++i]);
        } else if ((arg == "-m" || arg == "--model") && i + 1 < argc) {
            model_path = argv[++i];
        } else if ((arg == "-l" || arg == "--language") && i + 1 < argc) {
            language = argv[++i];
        } else if (arg == "-h" || arg == "--help") {
            print_usage(argv[0]);
            return 0;
        }
    }

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    std::cout << "========================================================\n";
    std::cout << "  Pokonyan C++ GPU Audio Client (Windows -> Pi)\n";
    std::cout << "========================================================\n";
    std::cout << "[Config] Target Raspberry Pi : http://" << pi_host << ":" << pi_port << "/\n";
    std::cout << "[Config] Whisper Model       : " << model_path << "\n";
    std::cout << "[Config] Language            : " << language << "\n";
    std::cout << "[Config] GPU Acceleration    : NVIDIA CUDA Active\n";
    std::cout << "--------------------------------------------------------\n";

    // Initialize Whisper Context
    struct whisper_context_params cparams = whisper_context_default_params();
    cparams.use_gpu = true;

    std::cout << "[System] Initializing Whisper model on GPU...\n";
    struct whisper_context* ctx = whisper_init_from_file_with_params(model_path.c_str(), cparams);
    if (!ctx) {
        std::cerr << "[Error] Failed to load model file: " << model_path << "\n";
        return 1;
    }
    std::cout << "[System] GPU Model loaded successfully!\n";

    // Initialize Microphone
    AudioBuffer audio_buffer(30, 16000);
    ma_device_config deviceConfig = ma_device_config_init(ma_device_type_capture);
    deviceConfig.capture.format   = ma_format_f32;
    deviceConfig.capture.channels = 1;
    deviceConfig.sampleRate       = 16000;
    deviceConfig.dataCallback     = data_callback;
    deviceConfig.pUserData        = &audio_buffer;

    ma_device device;
    if (ma_device_init(NULL, &deviceConfig, &device) != MA_SUCCESS) {
        std::cerr << "[Error] Failed to initialize microphone.\n";
        whisper_free(ctx);
        return 1;
    }
    if (ma_device_start(&device) != MA_SUCCESS) {
        std::cerr << "[Error] Failed to start microphone capture.\n";
        ma_device_uninit(&device);
        whisper_free(ctx);
        return 1;
    }

    std::cout << "[System] Microphone recording. Speak into your mic! (Ctrl+C to exit)\n\n";

    std::string accumulated_text = "";
    int quiet_cycles = 0;

    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(400));

        std::vector<float> pcm32 = audio_buffer.get_recent_samples(3, 16000);
        if (pcm32.size() < 16000 * 0.5) continue;

        struct whisper_full_params wparams = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
        wparams.print_progress   = false;
        wparams.print_special    = false;
        wparams.print_realtime   = false;
        wparams.print_timestamps = false;
        wparams.single_segment   = true;
        wparams.no_context       = true;
        wparams.n_threads        = 4;
        wparams.language         = language.c_str();

        if (whisper_full(ctx, wparams, pcm32.data(), (int)pcm32.size()) != 0) {
            continue;
        }

        const int n_segments = whisper_full_n_segments(ctx);
        std::string current_text = "";
        for (int i = 0; i < n_segments; ++i) {
            const char* text = whisper_full_get_segment_text(ctx, i);
            if (text) current_text += text;
        }

        auto trim = [](std::string& s) {
            s.erase(s.begin(), std::find_if(s.begin(), s.end(), [](unsigned char ch) { return !std::isspace(ch); }));
            s.erase(std::find_if(s.rbegin(), s.rend(), [](unsigned char ch) { return !std::isspace(ch); }).base(), s.end());
        };
        trim(current_text);

        if (current_text.empty()) {
            quiet_cycles++;
        } else {
            quiet_cycles = 0;
            accumulated_text = current_text;
            std::cout << "\r\033[K[Live Stream] " << current_text << std::flush;
        }

        // Sentence completion criteria:
        // 1. Text ends with sentence ending punctuation (., !, ?, 。)
        // 2. OR 2 cycles (~0.8 sec) of silence after text was spoken
        bool end_of_sentence = false;
        if (!accumulated_text.empty()) {
            char last_char = accumulated_text.back();
            if (last_char == '.' || last_char == '!' || last_char == '?' || last_char == '}') {
                end_of_sentence = true;
            } else if (quiet_cycles >= 2) {
                end_of_sentence = true;
            }
        }

        if (end_of_sentence) {
            std::cout << "\n\033[1;32m[Sentence Completed]\033[0m " << accumulated_text << "\n";
            std::cout << "[HTTP -> Pi " << pi_host << "] Sending to Pi Web Server..." << std::flush;

            bool sent_ok = send_text_to_pi(pi_host, pi_port, accumulated_text);
            if (sent_ok) {
                std::cout << " \033[1;32m[SUCCESS]\033[0m\n";
            } else {
                std::cout << " \033[1;31m[FAILED]\033[0m\n";
            }

            accumulated_text = "";
            quiet_cycles = 0;
            audio_buffer.clear();
        }
    }

    std::cout << "\n[System] Cleaning up and exiting...\n";
    ma_device_uninit(&device);
    whisper_free(ctx);
    return 0;
}
