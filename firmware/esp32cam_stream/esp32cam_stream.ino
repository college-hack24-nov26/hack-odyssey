// ESP32-CAM (AI-Thinker, OV2640): MJPEG over HTTP for the sewerbot dashboard.
//   http://<ip>:81/stream            multipart MJPEG (what the dashboard's Hardware tab reads)
//   http://<ip>/capture              single JPEG
//   http://<ip>/status               JSON
//   http://<ip>/control?var=framesize&val=8   (framesize 5=QVGA 6=CIF 8=VGA 9=SVGA 10=XGA; quality 4..63; brightness -2..2)
//   http://<ip>/led?v=0..255
// The camera node only streams: motors live on the other ESP32 so Wi-Fi + camera load never disturbs driving.
#include "esp_camera.h"
#include "esp_http_server.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include "config.h"

// ---- AI-Thinker pinout
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

static httpd_handle_t ctl_httpd = NULL, stream_httpd = NULL;
static const char* BOUNDARY = "frame";
static const char* STREAM_TYPE = "multipart/x-mixed-replace;boundary=frame";
static uint32_t frameCount = 0;

static esp_err_t stream_handler(httpd_req_t* req) {
  httpd_resp_set_type(req, STREAM_TYPE);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  char part[96];
  while (true) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { delay(20); continue; }
    esp_err_t res = httpd_resp_send_chunk(req, "\r\n--frame\r\n", 11);
    if (res == ESP_OK) {
      size_t n = snprintf(part, sizeof(part), "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", (unsigned)fb->len);
      res = httpd_resp_send_chunk(req, part, n);
    }
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;            // client went away
    frameCount++;
  }
  return ESP_OK;
}

static esp_err_t capture_handler(httpd_req_t* req) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t r = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return r;
}

static esp_err_t status_handler(httpd_req_t* req) {
  sensor_t* s = esp_camera_sensor_get();
  char b[260];
  snprintf(b, sizeof(b), "{\"framesize\":%d,\"quality\":%d,\"frames\":%u,\"rssi\":%d,\"heap\":%u,\"ip\":\"%s\"}",
           s->status.framesize, s->status.quality, (unsigned)frameCount, WiFi.RSSI(), (unsigned)ESP.getFreeHeap(),
           WiFi.localIP().toString().c_str());
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, b, HTTPD_RESP_USE_STRLEN);
}

static bool queryInt(httpd_req_t* req, const char* key, int& out) {
  char q[96], v[16];
  if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK) return false;
  if (httpd_query_key_value(q, key, v, sizeof(v)) != ESP_OK) return false;
  out = atoi(v);
  return true;
}

static esp_err_t control_handler(httpd_req_t* req) {
  char q[96], var[24];
  int val = 0;
  if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK || httpd_query_key_value(q, "var", var, sizeof(var)) != ESP_OK
      || !queryInt(req, "val", val)) { httpd_resp_send_404(req); return ESP_FAIL; }
  sensor_t* s = esp_camera_sensor_get();
  int r = -1;
  if (!strcmp(var, "framesize")) r = s->set_framesize(s, (framesize_t)val);
  else if (!strcmp(var, "quality")) r = s->set_quality(s, val);
  else if (!strcmp(var, "brightness")) r = s->set_brightness(s, val);
  else if (!strcmp(var, "contrast")) r = s->set_contrast(s, val);
  else if (!strcmp(var, "vflip")) r = s->set_vflip(s, val);
  else if (!strcmp(var, "hmirror")) r = s->set_hmirror(s, val);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_send(req, r == 0 ? "ok" : "bad request", HTTPD_RESP_USE_STRLEN);
  return ESP_OK;
}

static esp_err_t led_handler(httpd_req_t* req) {
  int v = 0;
  if (queryInt(req, "v", v)) ledcWrite(LED_PIN, constrain(v, 0, 255));
  return httpd_resp_send(req, "ok", HTTPD_RESP_USE_STRLEN);
}

static void startServers() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  httpd_uri_t cap = {"/capture", HTTP_GET, capture_handler, NULL};
  httpd_uri_t sta = {"/status", HTTP_GET, status_handler, NULL};
  httpd_uri_t ctl = {"/control", HTTP_GET, control_handler, NULL};
  httpd_uri_t led = {"/led", HTTP_GET, led_handler, NULL};
  if (httpd_start(&ctl_httpd, &cfg) == ESP_OK) {
    httpd_register_uri_handler(ctl_httpd, &cap);
    httpd_register_uri_handler(ctl_httpd, &sta);
    httpd_register_uri_handler(ctl_httpd, &ctl);
    httpd_register_uri_handler(ctl_httpd, &led);
  }
  cfg.server_port = 81;
  cfg.ctrl_port += 1;
  httpd_uri_t str = {"/stream", HTTP_GET, stream_handler, NULL};
  if (httpd_start(&stream_httpd, &cfg) == ESP_OK) httpd_register_uri_handler(stream_httpd, &str);
}

void setup() {
  Serial.begin(115200);
  ledcAttach(LED_PIN, 5000, 8);
  ledcWrite(LED_PIN, 0);

  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = XCLK_HZ;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = DEFAULT_FRAMESIZE;
  c.jpeg_quality = JPEG_QUALITY;
  c.fb_count = psramFound() ? 2 : 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;      // always the newest frame: lowest latency for driving
  if (esp_camera_init(&c) != ESP_OK) {
    Serial.println("Camera init failed - check the ribbon cable and the 5 V supply (brown-outs are the usual cause)");
    delay(3000);
    ESP.restart();
  }
  sensor_t* s = esp_camera_sensor_get();
  s->set_exposure_ctrl(s, 1);            // auto exposure: sewers are dark
  s->set_gain_ctrl(s, 1);
  s->set_aec2(s, 1);                     // night-mode style exposure algorithm
  s->set_ae_level(s, 1);
  s->set_gainceiling(s, GAINCEILING_32X);
  s->set_lenc(s, 1);                     // lens shading correction
  s->set_wpc(s, 1);
  s->set_bpc(s, 1);

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) delay(250);
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_FALLBACK_SSID, AP_FALLBACK_PASS);
    Serial.printf("Wi-Fi failed -> access point '%s'  http://%s:81/stream\n", AP_FALLBACK_SSID, WiFi.softAPIP().toString().c_str());
  } else {
    Serial.printf("Camera ready: http://%s:81/stream   (http://%s.local:81/stream)\n", WiFi.localIP().toString().c_str(), HOSTNAME);
  }
  WiFi.setSleep(false);
  MDNS.begin(HOSTNAME);
  startServers();
}

void loop() { delay(1000); }
