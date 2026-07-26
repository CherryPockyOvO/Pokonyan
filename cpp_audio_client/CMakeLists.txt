cmake_minimum_required(VERSION 3.18)
project(cpp_audio_client C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(MSVC)
    add_compile_options($<$<COMPILE_LANGUAGE:CXX>:/utf-8> $<$<COMPILE_LANGUAGE:CXX>:/wd4819>)
endif()

option(GGML_CUDA "ggml: enable CUDA" ON)
option(BUILD_SHARED_LIBS "Build shared libraries" OFF)

# Use cached whisper.cpp source directory
set(FETCHCONTENT_SOURCE_DIR_WHISPER_CPP "C:/Users/ZgZhi/Desktop/RealtimeSTT/cpp_realtime_stt/build/_deps/whisper_cpp-src" CACHE PATH "Path to local whisper.cpp source")

include(FetchContent)

FetchContent_Declare(
    whisper_cpp
    SOURCE_DIR "${FETCHCONTENT_SOURCE_DIR_WHISPER_CPP}"
)
FetchContent_MakeAvailable(whisper_cpp)

add_executable(cpp_audio_client main.cpp)

target_include_directories(cpp_audio_client PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${whisper_cpp_SOURCE_DIR}/include
    ${whisper_cpp_SOURCE_DIR}/ggml/include
)

target_link_libraries(cpp_audio_client PRIVATE whisper winhttp)

if(WIN32)
    target_link_libraries(cpp_audio_client PRIVATE winmm ole32 avrt)
endif()
