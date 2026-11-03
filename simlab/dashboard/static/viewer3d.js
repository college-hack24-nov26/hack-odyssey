"use strict";
/* Viewer3D: draws a simlab scene (the backend's scene() JSON) with three.js and moves it with streamed poses.
   World frame is MuJoCo's: z up, x forward, quaternions (w, x, y, z). Surroundings (sky, fog, sun, ground, animated water) come from env.js. */
class Viewer3D {
  /* opts (all optional; the defaults are what the Lab and the Builder have always had):
       interactive  drag/wheel/touch move the camera; false lets the page scroll over the view (a card inside a chat)
       shadows      sun shadows; false saves a 2048 px shadow map for small thumbnails
       maxDpr       cap on the device pixel ratio
       paused       do not draw (a view that is not on screen); also paused automatically while scrolled out of sight */
  constructor(container, opts = {}) {
    this.opts = { interactive: true, shadows: true, maxDpr: 2, paused: false, ...opts };
    this.interactive = this.opts.interactive;
    this.paused = this.opts.paused;
    this._visible = true;
    this.el = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, this.opts.maxDpr));
    this.renderer.shadowMap.enabled = this.opts.shadows;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    container.prepend(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.02, 2000);
    this.camera.up.set(0, 0, 1);
    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.env = new SimEnvironment(this);
    this.env.setTheme("grid", [10, 10]);
    this.groups = [];
    this.vehicleBody = 0;
    this.mode = "follow";
    this.target = new THREE.Vector3();
    this.az = -2.2; this.el_ = 0.55; this.dist = 6;
    this.trail = null; this.trailPts = [];
    this._bindControls();
    this._ro = new ResizeObserver(() => this.resize());
    this._ro.observe(container);
    if (window.IntersectionObserver) {                             // a view scrolled out of sight stops drawing (a chat may hold several)
      this._io = new IntersectionObserver(es => { this._visible = es[es.length - 1].isIntersecting; });
      this._io.observe(container);
    }
    this._raf = requestAnimationFrame(() => this._loop());
    this.resize();
  }

  /* ---------------------------------------------------------------- scene */
  clear() {
    for (const o of [...this.world.children]) {
      this.world.remove(o);
      o.traverse(c => { c.geometry?.dispose(); if (c.material) (Array.isArray(c.material) ? c.material : [c.material]).forEach(m => { m.map?.dispose(); m.dispose(); }); });
    }
    this.groups = []; this.trail = null; this.trailPts = [];
  }

  loadScene(sc) {
    this.clear();
    this.vehicleBody = sc.vehicle_body;
    this.vehicleSize = sc.vehicle_size;
    this.guide = null;
    this.theme = sc.theme || "grid";
    this.env.setTheme(this.theme, sc.arena);
    this.names = Object.fromEntries(sc.bodies.map((n, i) => [n, i]));
    this.groups = sc.bodies.map(() => { const g = new THREE.Group(); this.world.add(g); return g; });
    const build = sc.visual && window.SimModels && SimModels[sc.visual.model];
    const hidden = build ? new Set(sc.vehicle_bodies || []) : new Set();           // a detailed model replaces the plain collision shapes of the vehicle
    for (const g of sc.geoms) {
      if (hidden.has(g.body) && !g.part) continue;                                  // (parts the Builder preview puts inside the machine stay)
      const mesh = this._geomMesh(g, sc);
      if (!mesh) continue;
      mesh.position.set(g.pos[0], g.pos[1], g.pos[2]);
      mesh.quaternion.set(g.quat[1], g.quat[2], g.quat[3], g.quat[0]);
      this.groups[g.body].add(mesh);
    }
    this.model = null;
    if (build) {
      try {
        this.model = build(sc.visual);
        this.groups[this.vehicleBody].add(this.model.root);
        for (const [name, part] of Object.entries(this.model.parts || {})) if (this.names[name] !== undefined) this.groups[this.names[name]].add(part);
        if (sc.visual.ghost) {                                                      // the Builder shows the machine see-through, so the parts inside can be seen
          this._ghost(this.model.root);
          Object.values(this.model.parts || {}).forEach(p => this._ghost(p));
        }
      } catch (e) { console.warn("vehicle model failed", e); }
    }
    if (this.theme === "pipe") {                                   // the vehicle's own lamp lights the pipe
      const lamp = new THREE.PointLight(0xfff0d0, 1.4, 12, 1.6);
      lamp.position.set(0.05, 0, 0.05);
      this.groups[this.vehicleBody].add(lamp);
    }
    const trailGeo = new THREE.BufferGeometry();
    trailGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3 * 900), 3));
    trailGeo.setDrawRange(0, 0);
    this.trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: 0x4da3ff }));
    this.trail.frustumCulled = false;
    this.world.add(this.trail);
    this.sceneSize = Math.max(sc.arena[0], sc.arena[1]);
    this.target.set(0, 0, 0);
    this.setMode(this.mode);
  }

  _ghost(obj) {
    const seen = new Set();
    obj.traverse(o => {
      if (!o.isMesh) return;
      o.castShadow = false;
      for (const m of [].concat(o.material)) {
        if (seen.has(m)) continue;                                                  // materials are shared between meshes: change each once
        seen.add(m);
        m.transparent = true; m.opacity = Math.min(m.opacity ?? 1, 0.3); m.depthWrite = false; m.needsUpdate = true;
      }
    });
  }

  _concrete() {
    const c = document.createElement("canvas");
    c.width = c.height = 256;
    const x = c.getContext("2d");
    x.fillStyle = "#6f767e"; x.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 1800; i++) { const v = 90 + Math.random() * 60 | 0; x.fillStyle = `rgba(${v},${v + 3},${v + 8},0.35)`; x.fillRect(Math.random() * 256, Math.random() * 256, 2, 2); }
    x.strokeStyle = "rgba(30,34,40,0.55)"; x.lineWidth = 2; x.strokeRect(1, 1, 254, 254);
    const tex = new THREE.CanvasTexture(c);
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.anisotropy = 8;
    return tex;
  }

  _geomMesh(g, sc) {
    if (window.SimModels) {                                                       // items with a proper model: the person to rescue, floating debris
      const owner = sc.bodies[g.body];                                              // an item's geom is called <item>_geom: match on the body
      if (owner === "person" && SimModels.person) return SimModels.person(g.size);
      if (/^debris\d+$/.test(owner) && SimModels.debris) return SimModels.debris(g.size);
    }
    const [r, gr, b, a] = g.rgba;
    const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(r, gr, b), roughness: 0.62, metalness: 0.08, transparent: a < 1, opacity: a });
    let geo, shadow = true;
    const s = g.size;
    switch (g.type) {
      case "plane": {
        if (sc.theme === "water" && this.env.hasWater()) return null;                  // the animated water surface replaces the flat plane
        geo = new THREE.PlaneGeometry(2 * s[0], 2 * s[1]);
        shadow = false;
        if (sc.theme === "water") { mat.color.set(0x1b5f8c); mat.transparent = true; mat.opacity = 0.92; break; }
        const tex = this._concrete();
        tex.repeat.set(Math.round(2 * s[0]) / 2, Math.round(2 * s[1]) / 2);
        mat.map = tex; mat.color.set(0xffffff); mat.roughness = 0.95;
        break;
      }
      case "box": geo = new THREE.BoxGeometry(2 * s[0], 2 * s[1], 2 * s[2]); break;
      case "sphere": geo = new THREE.SphereGeometry(s[0], 24, 16); break;
      case "ellipsoid": geo = new THREE.SphereGeometry(1, 24, 16); geo.scale(s[0], s[1], s[2]); break;
      case "cylinder": geo = new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 32); geo.rotateX(Math.PI / 2); break;
      case "capsule": {                                    // r128 has no CapsuleGeometry: cylinder + two caps
        const grp = new THREE.Group();
        const cyl = new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 20); cyl.rotateX(Math.PI / 2);
        grp.add(new THREE.Mesh(cyl, mat));
        for (const sign of [1, -1]) { const m = new THREE.Mesh(new THREE.SphereGeometry(s[0], 16, 10), mat); m.position.z = sign * s[1]; grp.add(m); }
        grp.traverse(o => { o.castShadow = true; });
        return grp;
      }
      case "mesh": {
        const m = sc.meshes?.[g.mesh];
        if (!m) return null;
        geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(m.verts), 3));
        geo.setIndex(m.faces);
        geo.computeVertexNormals();
        mat.side = THREE.DoubleSide;
        if (window.SimModels && SimModels.pipeMaterial && /^(pipe_|chamber_)/.test(g.name)) return new THREE.Mesh(geo, SimModels.pipeMaterial(g.name, g.rgba, geo));
        if (/^defect_/.test(g.name)) { mat.transparent = false; mat.opacity = 1; mat.roughness = 0.4; mat.polygonOffset = true; mat.polygonOffsetFactor = -3; }
        break;
      }
      default: return null;
    }
    const mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = shadow; mesh.receiveShadow = true;
    return mesh;
  }

  /* ---------------------------------------------------------------- per-frame update */
  update(poses, env) {
    this.env.update(env);
    for (let i = 0; i < this.groups.length && i < poses.length; i++) {
      const p = poses[i], g = this.groups[i];
      g.position.set(p[0], p[1], p[2]);
      g.quaternion.set(p[4], p[5], p[6], p[3]);
    }
    const v = poses[this.vehicleBody];
    if (v && this.trail) {
      const last = this.trailPts[this.trailPts.length - 1];
      if (!last || Math.hypot(v[0] - last[0], v[1] - last[1], v[2] - last[2]) > 0.03) {
        this.trailPts.push([v[0], v[1], Math.max(v[2], 0.03)]);
        if (this.trailPts.length > 900) this.trailPts.shift();
        const arr = this.trail.geometry.attributes.position.array;
        this.trailPts.forEach((q, k) => { arr[3 * k] = q[0]; arr[3 * k + 1] = q[1]; arr[3 * k + 2] = q[2]; });
        this.trail.geometry.attributes.position.needsUpdate = true;
        this.trail.geometry.setDrawRange(0, this.trailPts.length);
      }
      this._vehicle = v;
    }
  }

  resetTrail() { this.trailPts = []; this.trail?.geometry.setDrawRange(0, 0); }
  setMode(m) {
    this.mode = m;
    const size = this.sceneSize || 10;
    if (m === "top") { this.el_ = 1.5; this.dist = size * 0.95; this.target.set(0, 0, 0); }
    else if (m === "follow") { this.el_ = 0.55; this.dist = Math.max(2.0, (this.vehicleSize || 0.35) * 9); }
  }

  /* Tint the active waypoint (markers carry the scene body name they refer to) and draw a guide line to it. */
  setMarkers(markers) {
    if (!this.names) return;
    let active = null;
    for (const m of markers || []) {
      if (!m.body || this.names[m.body] === undefined) continue;
      const g = this.groups[this.names[m.body]];
      g.traverse(o => { if (o.material && o.material.color) o.material.color.set(m.active ? 0xffd34d : 0x35d0ea); });
      if (m.active) active = m.pos;
    }
    const v = this._vehicle;
    if (!this.guide) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
      this.guide = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0xffd34d, transparent: true, opacity: 0.6 }));
      this.guide.frustumCulled = false;
      this.world.add(this.guide);
    }
    this.guide.visible = !!(active && v);
    if (active && v) {
      const a = this.guide.geometry.attributes.position.array;
      a.set([v[0], v[1], v[2], active[0], active[1], active[2]]);
      this.guide.geometry.attributes.position.needsUpdate = true;
    }
  }

  /* ---------------------------------------------------------------- camera and input */
  _loop() {
    this._raf = requestAnimationFrame(() => this._loop());
    if (document.hidden || this.paused || !this._visible || !this.el.clientWidth) return;           // nothing to draw: do not burn the GPU in a hidden tab or off screen
    const v = this._vehicle;
    if (v && this.mode === "follow") this.target.lerp(new THREE.Vector3(v[0], v[1], v[2]), 0.15);
    const ce = Math.cos(this.el_), d = this.dist;
    this.camera.position.set(this.target.x + d * ce * Math.cos(this.az), this.target.y + d * ce * Math.sin(this.az), this.target.z + d * Math.sin(this.el_));
    if (this.theme === "water" && this.camera.position.z < 0.3) this.camera.position.z = 0.3;       // keep the camera above the waves
    this.camera.lookAt(this.target);
    this.env.follow(this.target, performance.now() / 1000);
    this.renderer.render(this.scene, this.camera);
  }

  _bindControls() {
    const el = this.renderer.domElement;
    let drag = null;
    this.setInteractive(this.interactive);
    el.addEventListener("pointerdown", e => { if (!this.interactive) return; drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey }; el.setPointerCapture(e.pointerId); });
    el.addEventListener("pointerup", () => { drag = null; });
    el.addEventListener("contextmenu", e => e.preventDefault());
    el.addEventListener("pointermove", e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
      if (drag.pan) {
        this.mode = "orbit";
        const k = this.dist * 0.0016;
        this.target.x += (-Math.sin(this.az) * dx + Math.cos(this.az) * dy) * k * -1;
        this.target.y += (Math.cos(this.az) * dx + Math.sin(this.az) * dy) * k * -1;
      } else {
        this.az -= dx * 0.006;
        this.el_ = Math.max(0.05, Math.min(1.55, this.el_ + dy * 0.006));
      }
    });
    el.addEventListener("wheel", e => {
      if (!this.interactive) return;                               // not interactive: the wheel scrolls the page
      e.preventDefault();
      this.dist = Math.max(0.4, Math.min(300, this.dist * Math.exp(e.deltaY * 0.001)));
    }, { passive: false });
  }

  /* Turn camera control on or off at run time (a card is not interactive until it is focused or opened full screen). */
  setInteractive(on) {
    this.interactive = !!on;
    this.renderer.domElement.style.touchAction = this.interactive ? "none" : "pan-y";
    this.renderer.domElement.style.cursor = this.interactive ? "grab" : "";
  }

  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  dispose() {
    cancelAnimationFrame(this._raf); this._ro.disconnect(); this._io?.disconnect();
    this.clear();
    this.env.dispose?.();
    this.renderer.dispose();
    this.renderer.forceContextLoss();                              // free the GPU context now: browsers keep only about 16 alive
    this.renderer.domElement.remove();
  }
}
window.Viewer3D = Viewer3D;
