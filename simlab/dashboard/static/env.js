"use strict";
/* SimEnvironment: the surroundings of the 3D view: sky dome, fog, sun and shadows, ground and an animated water surface.
   The water's vertex shader evaluates the SAME five linear waves as the physics (BoatPlant.surface in simlab/plugins/boat.py) from the parameters the
   server streams in `env.wave`, so the boat you see rides the waves the simulation used. Offline: only three.js core is needed. */
class SimEnvironment {
  constructor(viewer) {
    this.v = viewer;
    this.scene = viewer.scene;
    this.theme = null;
    this.hemi = new THREE.HemisphereLight(0xcfe6ff, 0x5a6b78, 0.7);
    this.sun = new THREE.DirectionalLight(0xfff1dc, 1.15);
    this.sunDir = new THREE.Vector3(0.38, -0.52, 0.76).normalize();
    this.sun.castShadow = viewer.opts ? viewer.opts.shadows : true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0004;
    this.scene.add(this.hemi, this.sun, this.sun.target);
    this.sky = this._makeSky();
    this.scene.add(this.sky);
    this.water = null; this.ground = null;
    this.waveEnv = null;
  }

  dispose() {
    for (const o of [this.water, this.ground, this.sky]) { if (!o) continue; this.scene.remove(o); o.geometry?.dispose(); o.material?.dispose(); }
    this.water = this.ground = null;
    this.sun.shadow.map?.dispose();
  }

  /* ---------------------------------------------------------------- themes */
  static palette(theme) {
    if (theme === "water") return { zenith: 0x2f6fb5, horizon: 0xc4dcef, fog: 0xc4dcef, near: 80, far: 520, sun: 1.2, hemi: 0.75, ground: 0x1c4a68 };
    if (theme === "pipe") return { zenith: 0x040507, horizon: 0x06080b, fog: 0x040507, near: 6, far: 90, sun: 0.0, hemi: 0.95, ground: 0x151719 };
    return { zenith: 0x4a82c2, horizon: 0xdbe7f1, fog: 0xdbe7f1, near: 70, far: 520, sun: 1.1, hemi: 0.7, ground: 0x6c7a5e };
  }

  setTheme(theme, arena) {
    this.theme = theme;
    const p = SimEnvironment.palette(theme);
    this.p = p;
    this.scene.fog = new THREE.Fog(p.fog, p.near, p.far);
    this.v.renderer.setClearColor(p.fog);
    this.hemi.intensity = p.hemi;
    this.hemi.color.set(theme === "pipe" ? 0x9fb3c8 : 0xcfe6ff); this.hemi.groundColor.set(theme === "pipe" ? 0x3a3f45 : 0x5a6b78);
    this.sun.intensity = p.sun;
    this.sun.visible = p.sun > 0;
    const m = this.sky.material.uniforms;
    m.uZenith.value.set(p.zenith); m.uHorizon.value.set(p.horizon); m.uSun.value.copy(this.sunDir);
    this.sky.visible = theme !== "pipe";
    if (this.water) { this.scene.remove(this.water); this.water.geometry.dispose(); this.water.material.dispose(); this.water = null; }
    if (this.ground) { this.scene.remove(this.ground); this.ground.geometry.dispose(); this.ground.material.dispose(); this.ground = null; }
    if (theme === "water") {
      try { this.water = this._makeWater(p); this.scene.add(this.water); } catch (e) { console.warn("water shader failed", e); this.water = null; }
    } else if (theme === "grid") {
      this.ground = new THREE.Mesh(new THREE.PlaneGeometry(3000, 3000), new THREE.MeshStandardMaterial({ color: p.ground, roughness: 1, metalness: 0 }));
      this.ground.position.z = -0.03; this.ground.receiveShadow = true;
      this.scene.add(this.ground);
    }
    this.arena = arena;
  }

  hasWater() { return !!this.water; }

  /* ---------------------------------------------------------------- per frame */
  update(env) {
    this.waveEnv = env && env.wave ? env.wave : null;
    if (!this.water) return;
    const u = this.water.material.uniforms, w = this.waveEnv;
    if (w && w.hs > 0) {
      u.uHs.value = w.hs; u.uTp.value = Math.max(w.tp, 0.3); u.uDir.value = w.dir; u.uTime.value = w.t;
      for (let i = 0; i < 5; i++) { u.uPhase.value[i] = w.phase[i]; u.uShare.value[i] = w.share[i]; u.uPer.value[i] = w.per[i]; }
    } else {
      u.uHs.value = 0; if (w) u.uTime.value = w.t;
    }
    const wind = env && env.wind ? env.wind.ms : 0;
    u.uChop.value = Math.min(1, 0.25 + wind / 14);
  }

  follow(target, time) {
    if (this.water) {
      this.water.material.uniforms.uCenter.value.set(target.x, target.y);
      this.water.material.uniforms.uClock.value = time;
    }
    this.sky.position.copy(this.v.camera.position);
    const s = this.v.vehicleSize || 1, k = 6 + s * 6;
    this.sun.position.set(target.x + this.sunDir.x * 40, target.y + this.sunDir.y * 40, target.z + this.sunDir.z * 40);
    this.sun.target.position.copy(target);
    const c = this.sun.shadow.camera;
    if (c.right !== k) { c.left = -k; c.right = k; c.top = k; c.bottom = -k; c.near = 1; c.far = 120; c.updateProjectionMatrix(); }
  }

  /* ---------------------------------------------------------------- sky */
  _makeSky() {
    const mat = new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false, fog: false,
      uniforms: { uZenith: { value: new THREE.Color(0x4a82c2) }, uHorizon: { value: new THREE.Color(0xdbe7f1) }, uSun: { value: new THREE.Vector3(0.4, -0.5, 0.75) } },
      vertexShader: "varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: `uniform vec3 uZenith, uHorizon, uSun; varying vec3 vDir;
        void main(){
          vec3 d = normalize(vDir);
          float t = pow(clamp(d.z, 0.0, 1.0), 0.55);
          vec3 col = mix(uHorizon, uZenith, t);
          if (d.z < 0.0) col = mix(uHorizon, uHorizon * 0.7, clamp(-d.z * 4.0, 0.0, 1.0));
          float s = max(dot(d, normalize(uSun)), 0.0);
          col += vec3(1.0, 0.93, 0.8) * (pow(s, 600.0) * 2.5 + pow(s, 12.0) * 0.18);
          gl_FragColor = vec4(col, 1.0);
          #include <tonemapping_fragment>
        }`
    });
    const m = new THREE.Mesh(new THREE.SphereGeometry(900, 32, 16), mat);
    m.frustumCulled = false; m.renderOrder = -10;
    return m;
  }

  /* ---------------------------------------------------------------- water */
  _waterGeometry() {
    // polar grid around the viewer: fine near the boat (0.5 m), coarser far away, so the waves are resolved where it matters
    const rings = 120, segs = 96, q = 1.03, r0 = 0.5;
    const pos = new Float32Array((rings + 1) * (segs + 1) * 3), idx = [];
    for (let j = 0; j <= rings; j++) {
      const r = r0 * (Math.pow(q, j) - 1) / (q - 1);
      for (let i = 0; i <= segs; i++) {
        const a = 2 * Math.PI * i / segs, o = 3 * (j * (segs + 1) + i);
        pos[o] = r * Math.cos(a); pos[o + 1] = r * Math.sin(a); pos[o + 2] = 0;
      }
    }
    for (let j = 0; j < rings; j++) for (let i = 0; i < segs; i++) {
      const a = j * (segs + 1) + i, b = a + 1, c = a + segs + 1, d = c + 1;
      idx.push(a, b, c, b, d, c);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setIndex(idx);
    return g;
  }

  _makeWater(p) {
    const common = `
      uniform float uTime, uHs, uTp, uDir; uniform float uPhase[5]; uniform float uShare[5]; uniform float uPer[5]; uniform vec2 uCenter;
      const float G = 9.81;
      void waves(vec2 p, out float h, out vec2 grad) {
        h = 0.0; grad = vec2(0.0);
        float sigma2 = (uHs / 4.0) * (uHs / 4.0);
        vec2 d = vec2(cos(uDir), sin(uDir));
        for (int i = 0; i < 5; i++) {
          float tp = max(uTp * uPer[i], 0.3), om = 6.2831853 / tp, k = om * om / G, a = sqrt(2.0 * sigma2 * uShare[i]);
          float ph = k * dot(p, d) - om * uTime + uPhase[i];
          h += a * cos(ph); grad += -a * k * sin(ph) * d;
        }
      }`;
    const mat = new THREE.ShaderMaterial({
      side: THREE.DoubleSide, fog: false, transparent: false,
      uniforms: {
        uTime: { value: 0 }, uClock: { value: 0 }, uHs: { value: 0 }, uTp: { value: 3 }, uDir: { value: 0 }, uChop: { value: 0.3 },
        uPhase: { value: [0, 0, 0, 0, 0] }, uShare: { value: [0.1, 0.2, 0.4, 0.2, 0.1] }, uPer: { value: [0.8, 0.9, 1.0, 1.15, 1.3] },
        uCenter: { value: new THREE.Vector2() }, uDeep: { value: new THREE.Color(0x0a3a5c) }, uShallow: { value: new THREE.Color(0x2f8aa3) },
        uHorizon: { value: new THREE.Color(p.horizon) }, uZenith: { value: new THREE.Color(p.zenith) }, uSun: { value: this.sunDir.clone() },
        uFog: { value: new THREE.Color(p.fog) }, uFogNear: { value: p.near }, uFogFar: { value: p.far }
      },
      vertexShader: `${common}
        varying vec3 vWorld; varying vec3 vN; varying float vH;
        void main(){
          vec2 wp = position.xy + uCenter; float h; vec2 g; waves(wp, h, g);
          vWorld = vec3(wp, h); vH = h; vN = normalize(vec3(-g, 1.0));
          gl_Position = projectionMatrix * viewMatrix * vec4(vWorld, 1.0);
        }`,
      fragmentShader: `uniform vec3 uDeep, uShallow, uHorizon, uZenith, uSun, uFog; uniform float uFogNear, uFogFar, uHs, uClock, uChop; varying vec3 vWorld; varying vec3 vN; varying float vH;
        float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
          return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
        void main(){
          vec3 V = normalize(cameraPosition - vWorld);
          // cosmetic ripples in the normal only (they do not move the surface, so the picture stays true to the physics)
          vec2 q = vWorld.xy * 0.9 + vec2(uClock * 0.35, -uClock * 0.22);
          vec2 rip = vec2(noise(q) - 0.5, noise(q + 17.3) - 0.5) + 0.5 * vec2(noise(q * 2.3 + 4.1) - 0.5, noise(q * 2.3 - 9.7) - 0.5);
          vec3 N = normalize(vN + vec3(rip * 0.22 * uChop, 0.0));
          float fres = pow(1.0 - max(dot(N, V), 0.0), 4.0);
          vec3 R = reflect(-V, N);
          vec3 sky = mix(uHorizon, uZenith, pow(clamp(R.z, 0.0, 1.0), 0.55));
          float spec = pow(max(dot(R, normalize(uSun)), 0.0), 220.0);
          float depthMix = clamp(0.5 + vH * 1.6 / max(uHs, 0.2), 0.0, 1.0);
          vec3 body = mix(uDeep, uShallow, depthMix * 0.55);
          vec3 col = mix(body, sky, 0.12 + 0.88 * fres) + vec3(1.0, 0.95, 0.82) * spec * 1.6;
          float foam = uHs > 0.05 ? smoothstep(0.30 * uHs, 0.50 * uHs, vH) : 0.0;
          col = mix(col, vec3(0.93, 0.97, 1.0), foam * (0.35 + 0.6 * noise(vWorld.xy * 3.0 + uClock)));
          float dist = distance(cameraPosition, vWorld);
          col = mix(col, uFog, smoothstep(uFogNear, uFogFar, dist));
          gl_FragColor = vec4(col, 1.0);
          #include <tonemapping_fragment>
        }`
    });
    const m = new THREE.Mesh(this._waterGeometry(), mat);
    m.frustumCulled = false; m.renderOrder = -1;
    return m;
  }
}
window.SimEnvironment = SimEnvironment;
