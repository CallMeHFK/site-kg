/* WebGPU backend for the live scene.

   The scene data -- layout, colours, sizes, births, drag springs, finale
   state -- is built by SK.Renderer._initState/sync and is backend agnostic.
   This file replaces the draw calls with hand-written WGSL: the sky quad, the
   cortex sheet, the node sprites (planets and neurons share one fragment
   shader), their additive halos, the axons, the orbit rings and the people
   markers. Point sprites become instanced quads, because WebGPU has no
   gl_PointSize.

   If no adapter can be had, the canvas has not been touched yet, so the
   renderer mounts the WebGL backend on the same canvas and carries on. */
(function (SK) {
  "use strict";

  /* GLSL smoothstep is undefined for reversed edges, WGSL is undefined too;
     the shaders lean on reversed edges everywhere (a soft edge that falls
     outward), so every step goes through this and keeps GLSL semantics. */
  var WGSL_COMMON = [
    "fn ss(e0: f32, e1: f32, x: f32) -> f32 {",
    "  let t = clamp((x - e0) / (e1 - e0), 0.0, 1.0);",
    "  return t * t * (3.0 - 2.0 * t);",
    "}"
  ].join("\n");

  var SKY_WGSL = WGSL_COMMON + `
struct U {
  a: vec4f,   /* time, mode, pad, pad */
  b: vec4f,   /* drift.xy, res.xy */
};
@group(0) @binding(0) var<uniform> u: U;

fn hash2(p0: vec2f) -> f32 {
  var p = fract(p0 * vec2f(123.34, 456.21));
  p = p + vec2f(dot(p, p + vec2f(45.32)));
  return fract(p.x * p.y);
}
fn noise2(p0: vec2f) -> f32 {
  let i = floor(p0);
  var f = fract(p0);
  f = f * f * (3.0 - 2.0 * f);
  let a = hash2(i);
  let b = hash2(i + vec2f(1.0, 0.0));
  let c = hash2(i + vec2f(0.0, 1.0));
  let d = hash2(i + vec2f(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
fn fbm(p0: vec2f) -> f32 {
  var v = 0.0;
  var a = 0.5;
  var p = p0;
  for (var i = 0; i < 4; i = i + 1) {
    v = v + a * noise2(p);
    p = p * 2.02 + vec2f(17.3);
    a = a * 0.5;
  }
  return v;
}
fn stars(uv: vec2f, scale: f32, density: f32, size: f32, seed: f32, t: f32) -> vec3f {
  let g = uv * scale + vec2f(seed);
  let id = floor(g);
  let f = fract(g) - vec2f(0.5);
  let h = hash2(id + vec2f(seed));
  if (h > density) { return vec3f(0.0); }
  let off = vec2f(hash2(id + vec2f(3.1)), hash2(id + vec2f(7.7))) - vec2f(0.5);
  let d = length(f - off * 0.62);
  let tw = 0.42 + 0.58 * sin(t * (1.3 + 2.4 * h) + h * 61.0);
  let core = ss(size, 0.0, d);
  let glow = ss(size * 5.0, 0.0, d) * 0.22;
  let tint = mix(vec3f(0.72, 0.84, 1.0), vec3f(1.0, 0.84, 0.62), step(0.86, h));
  return tint * (core + glow) * tw;
}
fn comet(uv: vec2f, t: f32) -> f32 {
  let k = floor(t * 0.24);
  let h1 = hash2(vec2f(k, 1.7));
  let h2 = hash2(vec2f(k, 8.3));
  if (h1 > 0.45) { return 0.0; }
  let ph = fract(t * 0.24);
  let start = vec2f(-1.0 + h1 * 2.0, 0.60 + h2 * 0.30);
  let dir = normalize(vec2f(1.0, -0.62));
  let head = start + dir * (ph * 2.0);
  let q = uv - head;
  let along = -dot(q, dir);
  let perp = abs(dot(q, vec2f(-dir.y, dir.x)));
  let tail = ss(0.0, 0.02, along) * ss(0.42, 0.0, along) * ss(0.024, 0.0, perp);
  let core = ss(0.028, 0.0, length(q));
  let fade = (1.0 - ph) * (1.0 - ph);
  return (tail * 0.55 + core * 1.3) * fade;
}
fn cosmos(uv: vec2f, t: f32) -> vec3f {
  let flow = vec2f(sin(t * 0.105) * 0.105, cos(t * 0.082) * 0.082);
  let p = uv * 1.32 + u.b.xy + flow;
  let n1 = fbm(p + vec2f(t * 0.150, -t * 0.108));
  let n2 = fbm(p * 1.9 + vec2f(-t * 0.125, t * 0.140) + vec2f(7.0));
  let n3 = fbm(p * 3.4 + vec2f(t * 0.082, -t * 0.094) + vec2f(19.0));
  let mask = ss(0.26, 0.84, n1 * 0.68 + n2 * 0.34);
  let deep = vec3f(0.0035, 0.0045, 0.0130);
  var core = mix(vec3f(0.095, 0.068, 0.235), vec3f(0.018, 0.115, 0.190),
                 ss(0.40, 1.0, n2));
  core = mix(core, vec3f(0.225, 0.080, 0.220), ss(0.64, 1.0, n1) * 0.50);
  core = mix(core, vec3f(0.020, 0.165, 0.160), ss(0.70, 1.0, n3) * 0.38);
  var col = deep + core * mask * 1.05;
  col += vec3f(0.055, 0.052, 0.120) * ss(0.52, 1.0, n3) * 0.44;
  let lane = exp(-pow((uv.x * 0.78 + uv.y * 0.62) * 3.1, 2.0));
  col += vec3f(0.095, 0.105, 0.175) * lane * (0.60 + 0.40 * sin(t * 0.12));
  col += stars(uv + u.b.xy * 0.20 + vec2f(t * 0.0035, t * 0.0021),
               90.0, 0.68, 0.055, 1.0, t * 0.62);
  col += stars(uv + u.b.xy * 0.45 + vec2f(t * 0.0062, t * 0.0038),
               155.0, 0.56, 0.050, 9.0, t * 0.95) * 0.78;
  col += stars(uv + u.b.xy * 0.85 + vec2f(t * 0.0100, t * 0.0060),
               240.0, 0.48, 0.045, 23.0, t * 1.25) * 0.58;
  col += stars(uv + u.b.xy * 0.30 + vec2f(t * 0.0044, t * 0.0026) + vec2f(0.31, 0.17),
               320.0, mix(0.24, 0.76, lane), 0.038, 41.0, t * 0.9) * 0.62;
  col += vec3f(0.80, 0.88, 1.0) * comet(uv + u.b.xy * 0.12, t);
  return col;
}
fn deepvoid(uv: vec2f, t: f32) -> vec3f {
  let r = length(uv);
  var col = vec3f(0.0040, 0.0036, 0.0038);
  col += vec3f(0.024, 0.016, 0.018) * ss(1.02, 0.0, r);
  let g = uv * 175.0;
  let id = floor(g);
  let f = fract(g) - vec2f(0.5);
  let h = hash2(id);
  let mote = step(0.9962, h) * ss(0.42, 0.0, length(f));
  col += vec3f(0.40, 0.30, 0.22) * mote * (0.50 + 0.50 * sin(t * 0.8 + h * 37.0));
  return col;
}
/* City: a night sky lit from below by the streets. The glow sits where the
   skyline will be and the stars only survive above it, so the towers stand
   against haze rather than against a black plate. */
fn skyline(uv: vec2f, t: f32) -> vec3f {
  let h = uv.y;
  let zenith = vec3f(0.006, 0.008, 0.022);
  let low = vec3f(0.036, 0.032, 0.060);
  var col = mix(low, zenith, ss(-0.10, 0.52, h));
  let glow = exp(-pow(max(0.0, h + 0.04) * 3.4, 2.0));
  col += vec3f(0.30, 0.17, 0.070) * glow * 0.42;
  col += stars(uv + vec2f(t * 0.0022, 0.0), 170.0, 0.40, 0.042, 13.0, t * 0.55)
         * ss(0.04, 0.42, h);
  let band = sin(h * 9.0 + uv.x * 1.3 + t * 0.05) * 0.5 + 0.5;
  col += vec3f(0.022, 0.019, 0.030) * pow(band, 3.0)
         * ss(0.42, 0.02, abs(h - 0.12));
  return col;
}

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) uv: vec2f,
};
@vertex fn vs_sky(@builtin(vertex_index) vi: u32) -> VOut {
  var p: array<vec2f, 3> = array<vec2f, 3>(
    vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0));
  let xy = p[vi];
  var out: VOut;
  out.pos = vec4f(xy, 0.0, 1.0);
  out.uv = xy * 0.5 + vec2f(0.5);
  return out;
}
@fragment fn fs_sky(in: VOut) -> @location(0) vec4f {
  /* b.z is width and b.w is height, so the wide-screen stretch is w/h, the
     same scalar the GLSL sky uses. */
  let aspect = u.b.z / max(u.b.w, 1.0);
  let uv = (in.uv - vec2f(0.5)) * vec2f(aspect, 1.0);
  let t = u.a.x;
  var col = cosmos(uv, t);
  if (u.a.y > 1.5) { col = skyline(uv, t); }
  else if (u.a.y > 0.5) { col = deepvoid(uv, t); }
  let vig = ss(1.32, 0.26, length(in.uv - vec2f(0.5)));
  return vec4f(col * mix(0.45, 1.0, vig), 1.0);
}
`;

  var CORTEX_WGSL = WGSL_COMMON + `
struct U {
  a: vec4f,      /* time, fold, opacity, pad */
  light: vec4f,
  color: vec4f,
  rim: vec4f,
  model: mat4x4f,
  view: mat4x4f,
  proj: mat4x4f,
};
@group(0) @binding(0) var<uniform> u: U;

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) n: vec3f,
  @location(1) vv: vec3f,
  @location(2) fold: f32,
  @location(3) dev: f32,
};
@vertex fn vs_cortex(
  @location(0) position: vec3f,
  @location(1) normal: vec3f,
  @location(2) a_flat: vec3f,
  @location(3) a_flatN: vec3f,
  @location(4) a_dev: f32,
  @location(5) a_fold: f32
) -> VOut {
  let w = ss(a_dev - 0.25, a_dev + 0.05, u.a.y);
  let p = mix(a_flat, position, w);
  let n = normalize(mix(a_flatN, normal, w));
  let mv = u.view * u.model * vec4f(p, 1.0);
  var out: VOut;
  out.pos = u.proj * mv;
  out.n = n;
  out.vv = -mv.xyz;
  out.fold = a_fold;
  out.dev = a_dev;
  return out;
}
@fragment fn fs_cortex(in: VOut) -> @location(0) vec4f {
  let N = normalize(in.n);
  let V = normalize(in.vv);
  let L = normalize(u.light.xyz);
  let diff = max(dot(N, L), 0.0);
  let wrap = max((dot(N, L) + 0.45) / 1.45, 0.0);
  let fres = pow(1.0 - max(dot(N, V), 0.0), 4.0);
  let sulcus = 1.0 - ss(-0.085, -0.012, in.fold);
  let crown = ss(-0.012, 0.050, in.fold);
  let pulse = 0.92 + 0.08 * sin(u.a.x * 0.8 + in.dev * 9.0);
  var col = u.color.rgb * (0.50 + 0.80 * diff + 0.36 * wrap) * 1.9 * pulse;
  col += u.color.rgb * 0.10 * (0.5 + 0.5 * max(dot(N, V), 0.0));
  col = mix(col, col * 0.62, sulcus);
  col += u.color.rgb * crown * 0.14;
  let H = normalize(L + V);
  let spec = pow(max(dot(N, H), 0.0), 26.0) * (1.0 - sulcus);
  col += mix(u.rim.rgb, vec3f(1.0), 0.55) * spec * 0.32;
  col += u.rim.rgb * fres * 0.34;
  let a = u.a.z * (0.58 + 1.20 * fres) * (1.0 - 0.34 * sulcus);
  return vec4f(col, clamp(a, 0.0, 1.0));
}
`;

  /* One shader for planets and neurons: the node quad carries the whole cell,
     so the scene stays at one draw call per layer. */
  var NODE_WGSL = WGSL_COMMON + `
struct U {
  a: vec4f,      /* px, scale, minpx, time */
  b: vec4f,      /* fly, driftA, vol, halo */
  c: vec4f,      /* neural, minMul, gain, pad */
  cam: vec4f,
  light: vec4f,
  view: mat4x4f,
  proj: mat4x4f,
  res: vec4f,    /* device px */
  tint: vec4f,
};
@group(0) @binding(0) var<uniform> u: U;

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) c: vec3f,
  @location(1) d: f32,
  @location(2) r: f32,
  @location(3) age: f32,
  @location(4) grow: f32,
  @location(5) sprout: f32,
  @location(6) tail: vec3f,
  @location(7) seed: f32,
  @location(8) face: f32,
  @location(9) coord: vec2f,
};

@vertex fn vs_node(
  @builtin(vertex_index) vi: u32,
  @location(0) position: vec3f,
  @location(1) color: vec3f,
  @location(2) a_size: f32,
  @location(3) a_dim: f32,
  @location(4) a_ring: f32,
  @location(5) a_birth: f32,
  @location(6) a_seed: f32
) -> VOut {
  var q: array<vec2f, 6> = array<vec2f, 6>(
    vec2f(-1.0, -1.0), vec2f(1.0, -1.0), vec2f(1.0, 1.0),
    vec2f(-1.0, -1.0), vec2f(1.0, 1.0), vec2f(-1.0, 1.0));
  let corner = q[vi % 6u];
  let ph = a_seed;
  let age = max(0.0, u.a.w - a_birth);
  let born = ss(0.0, 0.45, age);
  let grow = clamp(age / 2.2, 0.0, 1.0);
  let sprout = clamp((age - 1.4) / 16.0, 0.0, 1.0);
  var face = 1.0;
  if (u.b.z > 0.5) {
    let pl = length(position);
    if (pl > 1e-4) {
      let nrm = position / pl;
      let toCam = u.cam.xyz - position;
      let tl = length(toCam);
      var f = 1.0;
      if (tl > 1e-4) { f = dot(nrm, toCam / tl); }
      face = mix(0.13, 1.0, ss(-0.55, 0.90, f));
    }
  }
  let k = clamp(age / 1.15, 0.0, 1.0);
  let ease = 1.0 - pow(1.0 - k, 2.4);
  let rr = length(position);
  var dir = vec3f(sin(ph * 6.283), cos(ph * 4.11), sin(ph * 2.77));
  if (rr > 1e-3) { dir = position / rr; }
  let side = normalize(cross(dir, vec3f(0.31, 0.91, 0.27)));
  let start = position + dir * (26.0 + 34.0 * ph) + side * (12.0 * (ph - 0.5));
  var p = position + (start - position) * u.b.x * (1.0 - ease);
  let flight = u.b.x * (1.0 - ease);
  let velV = (u.view * vec4f(position - start, 0.0)).xyz;
  let td = vec2f(velV.x, -velV.y);
  let tl2 = length(td);
  var tailDir = vec2f(0.0, 1.0);
  if (tl2 > 1e-4) { tailDir = td / tl2; }
  p = p + u.b.y * vec3f(sin(u.a.w * 0.55 + ph * 6.283),
                        cos(u.a.w * 0.47 + ph * 11.0),
                        sin(u.a.w * 0.63 + ph * 17.0));
  let swelled = (0.35 + 0.65 * born) * (1.0 + 0.18 * sin(born * 3.14159));
  let mv = u.view * vec4f(p, 1.0);
  var size = a_size * u.b.w * u.a.y * u.a.x * (300.0 / max(-mv.z, 1.0));
  size = size * mix(1.0, 1.42, a_ring) * swelled * (1.0 + flight * 5.0);
  if (u.b.z > 0.5) { size = size * mix(0.62, 1.0, face); }
  size = clamp(size, u.a.z * u.c.y, (220.0 + 280.0 * flight) * u.a.x);
  /* size is a diameter in device pixels, the way gl_PointSize reads it, so
     the quad's half-extent in NDC is size/res, not 2*size/res. */
  let clip = u.proj * mv;
  let ndc = clip.xy / clip.w
          + vec2f(corner.x, -corner.y) * (size / u.res.xy);
  var out: VOut;
  out.pos = vec4f(ndc * clip.w, clip.z, clip.w);
  out.c = color;
  out.d = a_dim;
  out.r = a_ring;
  out.age = born;
  out.grow = grow;
  out.sprout = sprout;
  out.tail = vec3f(tailDir, flight);
  out.seed = ph;
  out.face = face;
  out.coord = corner;
  return out;
}

@fragment fn fs_node(in: VOut) -> @location(0) vec4f {
  let sp = in.coord;
  var b = sp;
  let flight = in.tail.z;
  let tdir = in.tail.xy;
  let tperp = vec2f(-tdir.y, tdir.x);
  var sA = 0.0;
  var sC = in.c;
  if (flight > 0.002) {
    b = (b - tdir * 0.30 * flight) * (1.0 + 1.6 * flight);
    let hb = 0.30 * flight - dot(sp, tdir);
    let back = clamp(hb / (1.0 + 0.30 * flight), 0.0, 1.0);
    let w = mix(0.30, 0.03, back);
    let streak = ss(w, 0.0, abs(dot(sp, tperp))) * pow(1.0 - back, 1.2)
               * step(0.0, hb);
    sA = clamp(streak * min(1.0, 0.45 + 1.1 * flight), 0.0, 1.0);
    sC = mix(mix(in.c, vec3f(1.0), vec3f(0.35)), in.c, vec3f(back));
  }
  if (u.c.x > 0.5) {
    let sd = in.seed;
    let r = length(b);
    let coreR = 0.38 + 0.02 * sin(sd * 9.0);
    let cellR = coreR * (0.42 + 0.58 * ss(0.0, 0.30, in.grow));
    let ang = atan2(b.y, b.x);
    let bend = sin(ang * 3.0 + sd * 6.283) * 0.20
             + sin(ang * 7.0 - sd * 11.0) * 0.10;
    let rr = r * (1.0 + bend * ss(0.18, 0.95, r));
    let ad = atan2(b.y, b.x) + bend * 0.55 * ss(0.2, 1.0, r);
    let taper = 1.0 - 0.24 * ss(cellR * 1.2, 1.0, rr);
    let reach = mix(cellR * 0.85, 0.80 + 0.10 * in.sprout,
                    ss(0.0, 0.72, in.grow));
    let rrm = min(rr, reach);
    let a6 = ss(0.40, 0.96, cos(ad * 6.0 + sd * 6.283));
    var arms = a6 * ss(reach, cellR * 0.55, rrm)
             * ss(cellR * 0.35, cellR * 1.45, rrm);
    let a12 = ss(0.70, 0.99, cos(ad * 12.0 - sd * 9.0));
    arms += a12 * ss(reach * 0.88, cellR * 1.3, rrm)
          * ss(cellR * 0.55, cellR * 1.9, rrm) * 0.38
          * ss(0.30, 0.85, in.grow)
          * (0.30 + 0.70 * in.sprout);
    let bead = ss(0.72, 1.0, sin(rr * 15.0 - sd * 7.0) * 0.5 + 0.5);
    arms *= taper * (1.0 + 0.55 * bead * ss(0.55, 1.0, in.grow)
                     * (0.35 + 0.65 * in.sprout));
    arms *= 1.0 - 0.42 * ss(0.55, 1.0, rrm);
    let soma = ss(cellR + 0.07, cellR - 0.03, r);
    let membrane = ss(cellR + 0.07, cellR - 0.01, r)
                 - ss(cellR - 0.03, cellR - 0.13, r);
    let nu = b - vec2f(0.035, 0.045);
    let nucleus = ss(0.130, 0.0, length(nu));
    let aura = exp(-r * 2.8) * 0.24;
    var col = in.c * (0.44 + 1.38 * arms * (1.0 + 0.22 * in.sprout))
            + mix(in.c, u.tint.rgb, vec3f(0.55)) * soma * (1.02 + 0.16 * in.sprout)
            + u.tint.rgb * (membrane * 0.80 + nucleus * 1.18)
            + in.c * aura;
    var a = clamp(soma * 0.98 + arms * 0.78 + membrane * 0.45
                  + nucleus * 0.9 + aura, 0.0, 1.0);
    col = mix(col, sC, sA * (1.0 - soma));
    a = max(a, sA * (1.0 - soma));
    col *= in.face;
    a *= in.face;
    if (a < 0.02) { discard; }
    return vec4f(col, a * (0.18 + 0.82 * in.d) * in.age);
  }
  let scale = mix(1.0, 1.42, in.r);
  let p = b * scale;
  let r2 = dot(p, p);
  let inside = 1.0 - ss(0.96, 1.0, r2);
  let n = vec3f(p.x, -p.y, sqrt(max(0.0, 1.0 - r2)));
  let L = normalize(u.light.xyz);
  let diff = max(dot(n, L), 0.0);
  let spec = pow(max(dot(n, normalize(L + vec3f(0.0, 0.0, 1.0))), 0.0), 18.0);
  let rim = pow(1.0 - n.z, 2.6);
  let seed = fract(dot(in.c, vec3f(12.9898, 78.233, 37.719)));
  let landA = sin(p.x * 6.0 + seed * 19.0) * sin(p.y * 7.5 - seed * 11.0);
  let land = ss(0.10, 0.58, landA * 0.5 + 0.5);
  let cloud = ss(0.55, 0.96,
                 sin(p.y * 13.0 + seed * 6.28 + sin(p.x * 5.0) * 0.9) * 0.5 + 0.5);
  var albedo = mix(in.c * 0.84, in.c * 1.08, vec3f(land));
  albedo = mix(albedo, mix(in.c, vec3f(1.0), vec3f(0.5)), vec3f(cloud * 0.26));
  let polar = ss(0.62, 0.95, abs(p.y));
  albedo = mix(albedo, vec3f(0.80, 0.90, 1.0), vec3f(polar * 0.24 * seed));
  let rimC = mix(in.c, vec3f(0.78, 0.87, 1.0), vec3f(0.42));
  var col = albedo * (0.09 + 0.91 * diff)
          + vec3f(0.80, 0.90, 1.0) * spec * 0.34
          + rimC * rim * 1.05 + in.c * 0.040;
  let e = abs(length(vec2f(b.x, b.y * 3.4)) - 0.86);
  var band = (1.0 - ss(0.0, 0.035, e)) * in.r;
  band *= select(1.0, 1.0 - inside, b.y < 0.0);
  col += (in.c * 1.5 + vec3f(0.10)) * band * (0.4 + 0.6 * diff);
  var a = max(inside, band * 0.75);
  let sVis = sA * (1.0 - inside);
  col = mix(col, sC, vec3f(sVis));
  a = max(a, sVis);
  if (a < 0.01) { discard; }
  return vec4f(col, a * (0.15 + 0.85 * in.d) * in.age);
}

@fragment fn fs_halo(in: VOut) -> @location(0) vec4f {
  let p = in.coord;
  let r2 = dot(p, p);
  if (r2 > 1.0) { discard; }
  let tdir = in.tail.xy;
  let tperp = vec2f(-tdir.y, tdir.x);
  let flight = clamp(in.tail.z, 0.0, 1.0);
  let hp = p - tdir * 0.30 * flight;
  let hd = length(hp) * (1.0 + 2.0 * flight);
  let n = vec3f(hp.x, -hp.y, sqrt(max(0.0, 1.0 - min(hd * hd, 1.0))));
  let lit = 0.22 + 0.78 * max(dot(n, normalize(u.light.xyz)), 0.0);
  let sharp = mix(2.2, 1.5, u.c.x);
  let shade = mix(lit, 1.0, u.c.x * 0.75);
  let body = pow(ss(1.0, 0.0, min(hd, 1.0)), sharp) * u.c.z * in.d * shade;
  let hb = 0.30 * flight - dot(p, tdir);
  let back = clamp(hb / (1.0 + 0.30 * flight), 0.0, 1.0);
  let w = mix(0.16, 0.02, back);
  let tail = ss(w, 0.0, abs(dot(p, tperp))) * pow(1.0 - back, 2.6)
           * step(0.0, hb);
  let aura = tail * flight * 0.6 * in.d;
  return vec4f(in.c, (body + aura) * in.age * in.face);
}
`;

  var EDGE_WGSL = WGSL_COMMON + `
struct U {
  a: vec4f,      /* cull, opacity, sweepX, sweepAmp */
  b: vec4f,      /* time, neural, fly, driftA */
  cam: vec4f,
  view: mat4x4f,
  proj: mat4x4f,
};
@group(0) @binding(0) var<uniform> u: U;

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) c: vec3f,
  @location(1) a: f32,
};
@vertex fn vs_edge(
  @location(0) position: vec3f,
  @location(1) color: vec3f,
  @location(2) a_base: f32,
  @location(3) a_dim: f32,
  @location(4) a_birth: f32,
  @location(5) a_t: f32,
  @location(6) a_seed: f32
) -> VOut {
  let ph = a_seed;
  let age = max(0.0, u.b.x - a_birth);
  let k = clamp(age / 1.15, 0.0, 1.0);
  let ease = 1.0 - pow(1.0 - k, 2.4);
  let rr = length(position);
  var dir = vec3f(sin(ph * 6.283), cos(ph * 4.11), sin(ph * 2.77));
  if (rr > 1e-3) { dir = position / rr; }
  let side = normalize(cross(dir, vec3f(0.31, 0.91, 0.27)));
  let start = position + dir * (26.0 + 34.0 * ph) + side * (12.0 * (ph - 0.5));
  var pos = position + (start - position) * u.b.z * (1.0 - ease);
  pos = pos + u.b.w * vec3f(sin(u.b.x * 0.55 + ph * 6.283),
                            cos(u.b.x * 0.47 + ph * 11.0),
                            sin(u.b.x * 0.63 + ph * 17.0));
  let d = distance(pos, u.cam.xyz);
  let fade = 1.0 - ss(u.a.x * 0.55, u.a.x, d);
  let wave = ss(0.0, 0.5, age - a_t * 0.22);
  var amp = a_base * u.a.y * a_dim * fade * wave;
  amp *= mix(1.0, 2.6, u.b.y);
  amp += u.a.w * exp(-abs(pos.x - u.a.z) * 0.18) * 0.9;
  amp += u.b.y * a_base * 0.85
       * (0.5 + 0.5 * sin(pos.x * 0.55 + pos.y * 0.38 + pos.z * 0.31
                          + u.b.x * 2.1 + a_t * 4.0));
  var out: VOut;
  out.pos = u.proj * u.view * vec4f(pos, 1.0);
  out.c = color;
  out.a = amp;
  return out;
}
@fragment fn fs_edge(in: VOut) -> @location(0) vec4f {
  return vec4f(in.c, in.a);
}
`;

  var RING_WGSL = WGSL_COMMON + `
struct U {
  a: vec4f,      /* opacity, pad, pad, pad */
  model: mat4x4f,
  view: mat4x4f,
  proj: mat4x4f,
};
@group(0) @binding(0) var<uniform> u: U;

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) c: vec3f,
};
@vertex fn vs_ring(@location(0) position: vec3f, @location(1) color: vec3f) -> VOut {
  var out: VOut;
  out.pos = u.proj * u.view * u.model * vec4f(position, 1.0);
  out.c = color;
  return out;
}
@fragment fn fs_ring(in: VOut) -> @location(0) vec4f {
  return vec4f(in.c, u.a.x);
}
`;

  /* People mode: an instanced sphere per selected node. The scene data
     already packs a matrix and a colour per item; the sphere supplies the
     silhouette and the flat colour matches the old MeshBasicMaterial. */
  var EGO_WGSL = `
struct U {
  view: mat4x4f,
  proj: mat4x4f,
};
@group(0) @binding(0) var<uniform> u: U;

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) c: vec3f,
};
@vertex fn vs_ego(
  @location(0) sphere: vec3f,
  @location(1) centre: vec3f,
  @location(2) scale: f32,
  @location(3) color: vec3f
) -> VOut {
  var out: VOut;
  out.pos = u.proj * u.view * vec4f(centre + sphere * scale, 1.0);
  out.c = color;
  return out;
}
@fragment fn fs_ego(in: VOut) -> @location(0) vec4f {
  return vec4f(in.c, 0.94);
}
`;

  /* The city: one merged box mesh. Windows, street grid and the finale ring
     are all cut in the fragment shader, so the vertex stage only lifts the
     towers out of their plots. Depth is on for this pipeline alone -- the
     rest of the pass is painter-ordered and writes no depth. */
  var CITY_WGSL = WGSL_COMMON + `
struct U {
  a: vec4f,     /* time, padding, finale, city radius */
  b: vec4f,     /* street pitch */
  view: mat4x4f,
  proj: mat4x4f,
}
@group(0) @binding(0) var<uniform> u: U;

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) local: vec3f,
  @location(1) face: f32,
  @location(2) spec: vec4f,
  @location(3) col: vec3f,
  @location(4) nrm: vec3f,
  @location(5) world: vec3f,
  @location(6) depth: f32,
}

@vertex fn vs_city(
  @location(0) position: vec3f,
  @location(1) normal: vec3f,
  @location(2) color: vec3f,
  @location(3) local: vec3f,
  @location(4) face: f32,
  @location(5) spec: vec4f,
  @location(6) birth: f32) -> VOut {
  var o: VOut;
  let grow = ss(0.0, 0.85, max(0.0, u.a.x - birth));
  let p = vec3f(position.x, position.y * (grow + 0.00001), position.z);
  let mv = u.view * vec4f(p, 1.0);
  o.pos = u.proj * mv;
  o.local = local;
  o.face = face;
  o.spec = spec;
  o.col = color;
  o.nrm = normal;
  o.world = p;
  o.depth = -mv.z;
  return o;
}

fn h21(p0: vec2f) -> f32 {
  var p = fract(p0 * vec2f(127.31, 311.7));
  p = p + vec2f(dot(p, p + vec2f(34.23)));
  return fract(p.x * p.y);
}

fn hazeCol() -> vec3f { return vec3f(0.030, 0.036, 0.060); }

@fragment fn fs_city(i: VOut) -> @location(0) vec4f {
  var col = vec3f(0.0);
  var alpha = 1.0;
  if (i.face > 9.5) {
    let g = i.local.xy;
    let P = max(u.b.x, 0.5);
    let dx = abs(fract(g.x / P + 0.5) - 0.5) * P;
    let dz = abs(fract(g.y / P + 0.5) - 0.5) * P;
    /* Asphalt out to the kerb, a concrete pavement around the block, and a
       dashed centre line: the street has to explain where the towers stand. */
    let m = min(dx, dz);
    let kerb = P * 0.21;
    let road = 1.0 - ss(kerb - 0.10, kerb, m);
    let walk = ss(kerb - 0.015, kerb + 0.04, m) * (1.0 - ss(kerb + 0.26, kerb + 0.34, m));
    let along = select(g.x, g.y, dx < dz);
    let mark = (1.0 - ss(0.018, 0.055, m)) * step(0.52, fract(along * 0.42));
    let r = length(g) / max(u.a.w, 1.0);
    /* The pattern dissolves before it aliases: past half the city radius the
       street grid is one pixel wide and only reads as moire. */
    let near = 1.0 - ss(0.45, 0.88, r);
    col = mix(vec3f(0.040, 0.042, 0.052), vec3f(0.020, 0.022, 0.030), road * near);
    col = mix(col, vec3f(0.062, 0.064, 0.076), walk * near);
    col = col + vec3f(0.30, 0.19, 0.086) * road
        * (1.0 - ss(0.0, kerb, m)) * 0.05 * near;
    col = col + vec3f(0.40, 0.30, 0.16) * mark * 0.20 * near;
    col = mix(col, hazeCol(), ss(0.85, 1.60, r));
    /* Dither the ramp into the haze: 8-bit steps would band here. */
    col = col + vec3f((h21(g * 37.0) - 0.5) * 0.003);
    alpha = 1.0 - ss(1.15, 1.85, r);
  } else if (i.face > 8.5) {
    /* Setback terrace: bare roof with a lip, no beacon. */
    let e = max(max(abs(i.local.x), abs(i.local.y)),
                (abs(i.local.x) + abs(i.local.y)) * 0.5952);
    col = i.col * 0.18 + vec3f(0.020, 0.024, 0.034);
    col = col * (0.84 + 0.24 * fract(i.spec.z * 5.7));
    col = col + vec3f((h21(floor(i.local.xy * 9.0)) - 0.5) * 0.010);
    col = col + i.col * 0.36 * ss(0.86, 1.0, e);
  } else if (i.face > 7.5) {
    let e = max(max(abs(i.local.x), abs(i.local.y)),
                (abs(i.local.x) + abs(i.local.y)) * 0.5952);
    col = i.col * 0.15 + vec3f(0.018, 0.022, 0.032);
    col = col * (0.82 + 0.30 * fract(i.spec.z * 5.7));
    col = col + vec3f((h21(floor(i.local.xy * 9.0)) - 0.5) * 0.012);
    col = col + i.col * 0.44 * ss(0.86, 1.0, e);
    var beacon = 1.0 - ss(0.0, 0.13, length(i.local.xy));
    beacon = beacon * (0.40 + 1.45 * i.spec.y);
    beacon = beacon * (0.70 + 0.30 * sin(u.a.x * 2.1 + i.spec.z * 31.0));
    col = col + vec3f(1.0, 0.74, 0.42) * beacon * 1.25;
  } else {
    let N = normalize(i.nrm);
    /* One sun, not a studio rig: the faces turned away from it fall into
       shadow, and that contrast is what tells a chamfer from a flat slab. */
    let L = normalize(vec3f(0.45, 0.72, 0.33));
    let facing = 0.40 + 0.72 * max(dot(N, L), 0.0);
    let sky = 0.09 + 0.13 * (N.y * 0.5 + 0.5);
    col = i.col * (0.80 * facing + sky) * (0.46 + 0.54 * i.local.y)
        * vec3f(0.90, 0.97, 1.08);
    col = col * (0.82 + 0.30 * fract(i.spec.z * 5.7));
    col = col + i.col * 0.38 * ss(0.86, 1.0, abs(i.local.x));
    let up = 1.0 - i.local.y;
    col = col + vec3f(0.26, 0.17, 0.085) * up * up * up * 0.05;
    let floors = max(i.spec.x, 1.0);
    let uu = (i.local.x * 0.5 + 0.5) * max(i.spec.w, 1.0);
    let vv = i.local.y * floors;
    let cell = floor(vec2f(uu, vv));
    let f = fract(vec2f(uu, vv));
    let win = ss(0.24, 0.30, f.x) * (1.0 - ss(0.70, 0.76, f.x))
            * ss(0.28, 0.36, f.y) * (1.0 - ss(0.70, 0.78, f.y));
    let seed = i.spec.z;
    let pick = h21(cell + vec2f(seed * 37.0, seed * 11.0));
    var lit = step(pick, i.spec.y);
    let ring = u.a.z * 1.95 - 0.35;
    /* Plots start half a pitch in, so the wave has to start there too. */
    let q = i.world.xz - vec2f(max(u.b.x, 0.5) * 0.5);
    let dist = length(q) / max(u.a.w, 1.0);
    let band = ss(ring - 0.24, ring, dist) * (1.0 - ss(ring, ring + 0.15, dist));
    lit = max(lit, band * step(0.42, pick));
    var lamp = mix(vec3f(1.0, 0.84, 0.58), i.col, 0.22);
    lamp = mix(lamp, vec3f(0.80, 0.89, 1.0), step(0.88, h21(cell * 1.7 + seed * 5.3)) * 0.75);
    let flick = 0.86 + 0.14 * sin(u.a.x * 1.6 + cell.x * 2.7
                                + cell.y * 1.9 + seed * 9.0);
    col = col + vec3f(0.013, 0.016, 0.023) * win;
    /* A pale spandrel under every window: that is the floor line, and a tower
       with visible storeys stops reading as a coloured box. */
    let bnd = ss(0.84, 0.89, f.y) * (1.0 - ss(0.93, 0.97, f.y));
    col = col + i.col * 0.22 * bnd * (0.55 + 0.45 * lit);
    col = col + lamp * win * lit * flick * 1.45;
  }
  let haze = ss(u.a.w * 1.05, u.a.w * 2.30, i.depth);
  col = mix(col, hazeCol(), haze * 0.88);
  return vec4f(col, alpha);
}
`;

  var CITY_SLOTS = [
    {stride: 12, format: "float32x3", loc: 0, name: "position"},
    {stride: 12, format: "float32x3", loc: 1, name: "normal"},
    {stride: 12, format: "float32x3", loc: 2, name: "color"},
    {stride: 12, format: "float32x3", loc: 3, name: "a_local"},
    {stride: 4, format: "float32", loc: 4, name: "a_face"},
    {stride: 16, format: "float32x4", loc: 5, name: "a_spec"},
    {stride: 4, format: "float32", loc: 6, name: "a_birth"}
  ];
  var NODE_SLOTS = [
    {stride: 12, format: "float32x3", loc: 0, name: "position"},
    {stride: 12, format: "float32x3", loc: 1, name: "color"},
    {stride: 4, format: "float32", loc: 2, name: "a_size"},
    {stride: 4, format: "float32", loc: 3, name: "a_dim"},
    {stride: 4, format: "float32", loc: 4, name: "a_ring"},
    {stride: 4, format: "float32", loc: 5, name: "a_birth"},
    {stride: 4, format: "float32", loc: 6, name: "a_seed"}
  ];
  var CORTEX_SLOTS = [
    {stride: 12, format: "float32x3", loc: 0, name: "position"},
    {stride: 12, format: "float32x3", loc: 1, name: "normal"},
    {stride: 12, format: "float32x3", loc: 2, name: "a_flat"},
    {stride: 12, format: "float32x3", loc: 3, name: "a_flatN"},
    {stride: 4, format: "float32", loc: 4, name: "a_dev"},
    {stride: 4, format: "float32", loc: 5, name: "a_fold"}
  ];
  var EDGE_SLOTS = [
    {stride: 12, format: "float32x3", loc: 0, name: "position"},
    {stride: 12, format: "float32x3", loc: 1, name: "color"},
    {stride: 4, format: "float32", loc: 2, name: "a_base"},
    {stride: 4, format: "float32", loc: 3, name: "a_dim"},
    {stride: 4, format: "float32", loc: 4, name: "a_birth"},
    {stride: 4, format: "float32", loc: 5, name: "a_t"},
    {stride: 4, format: "float32", loc: 6, name: "a_seed"}
  ];

  function layoutOf(slots, stepMode) {
    return slots.map(function (s) {
      return {
        arrayStride: s.stride,
        stepMode: stepMode || "vertex",
        attributes: [{shaderLocation: s.loc, offset: 0, format: s.format}]
      };
    });
  }

  var ALPHA_BLEND = {
    color: {srcFactor: "src-alpha", dstFactor: "one-minus-src-alpha", operation: "add"},
    alpha: {srcFactor: "one", dstFactor: "one-minus-src-alpha", operation: "add"}
  };
  var ADD_BLEND = {
    color: {srcFactor: "src-alpha", dstFactor: "one", operation: "add"},
    alpha: {srcFactor: "one", dstFactor: "one", operation: "add"}
  };

  function GpuRenderer() {
    SK.Renderer.call(this);
    this.backend = "webgpu";
    this.gpuReady = false;
    this.gpuFailed = "";
    this._gl = false;
    this._disposed = false;
    this._ubos = {};
    this._vbs = new Map();
    this._ibs = new Map();
    this._pipelines = {};
    this._clear = {r: 0.016, g: 0.020, b: 0.043, a: 1};
    this._resW = 1;
    this._resH = 1;
  }
  GpuRenderer.prototype = Object.create(SK.Renderer.prototype);
  GpuRenderer.prototype.constructor = GpuRenderer;
  GpuRenderer.supported = function () {
    return typeof navigator !== "undefined" && !!navigator.gpu;
  };

  GpuRenderer.prototype.mount = function (canvas, live) {
    this._initState(canvas, live);
    this._initScratch();
    this.setScene("planets");
    this.resize();
    var self = this;
    /* The adapter probe is async, so mount cannot throw when WebGPU is
       missing. ready settles true once something is drawing on the canvas
       (WebGPU, or WebGL after an adapter failure) and false when neither
       backend could take it -- the caller then switches to the Canvas2D
       tier. */
    this.ready = this._initGpu().then(
      function () { return true; },
      function (err) {
        self._fallbackGL(err);
        return !!self._gl;
      });
    return this;
  };

  GpuRenderer.prototype.setScene = function (name) {
    SK.Renderer.prototype.setScene.call(this, name);
    var bg = this.cfg && this.cfg.bg;
    var hex = typeof bg === "number" ? bg : 0x04050b;
    this._clear = {r: ((hex >> 16) & 255) / 255,
                   g: ((hex >> 8) & 255) / 255,
                   b: (hex & 255) / 255, a: 1};
  };

  GpuRenderer.prototype._initGpu = async function () {
    if (!navigator.gpu) throw new Error("navigator.gpu is unavailable");
    var timer = 0;
    var adapter = await Promise.race([
      navigator.gpu.requestAdapter({powerPreference: "high-performance"})
        .finally(function () { clearTimeout(timer); }),
      new Promise(function (resolve, reject) {
        timer = setTimeout(function () {
          reject(new Error("WebGPU adapter probe timed out"));
        }, 8000);
      })
    ]);
    if (!adapter) throw new Error("no WebGPU adapter");
    var device = await adapter.requestDevice();
    /* The probe can outlive the renderer, or lose the race to the WebGL
       fallback; either way the device has no canvas to draw on. */
    if (this._disposed || this._gl) {
      try { device.destroy(); } catch (ignored) {}
      return;
    }
    var context = this.canvas.getContext("webgpu");
    if (!context) throw new Error("webgpu canvas context unavailable");
    var format = navigator.gpu.getPreferredCanvasFormat();
    context.configure({device: device, format: format, alphaMode: "opaque"});
    this.device = device;
    this._context = context;
    this._format = format;
    this._buildPipelines(format);
    var self = this;
    device.lost.then(function (info) {
      if (self._disposed) return;
      self.gpuFailed = info && info.message || "device lost";
      self.contextLost = true;
      /* A lost device cannot be revived, and the canvas can never hand out a
         webgl context now, so the page has to say so instead of freezing on
         the last frame. */
      if (self.onLost) self.onLost(self.gpuFailed);
    });
    this.gpuReady = true;
    this.colorDirty = true;
    this.lastVersion = -1;
    this._gpuResize();
  };

  GpuRenderer.prototype._fallbackGL = function (err) {
    if (this._gl || this.gpuReady || this._disposed) return;
    this.gpuFailed = String(err && err.message || err);
    if (this._context) {
      /* getContext("webgpu") already ran, so this element can never hand out
         a webgl or 2d context; the caller has to give the fallback tier a
         fresh canvas. */
      this.dead = true;
      return;
    }
    try {
      this._mountGL();
      this._gl = true;
      this.backend = "webgl";
      this.resize();
    } catch (mountErr) {
      this.gpuFailed += " / webgl: " + (mountErr && mountErr.message || mountErr);
      this.dead = true;
    }
  };

  GpuRenderer.prototype._buildPipelines = function (format) {
    var device = this.device;
    var modules = {
      sky: device.createShaderModule({code: SKY_WGSL}),
      cortex: device.createShaderModule({code: CORTEX_WGSL}),
      node: device.createShaderModule({code: NODE_WGSL}),
      edge: device.createShaderModule({code: EDGE_WGSL}),
      ring: device.createShaderModule({code: RING_WGSL}),
      ego: device.createShaderModule({code: EGO_WGSL}),
      city: device.createShaderModule({code: CITY_WGSL})
    };
    function pipeline(module, vs, fs, topology, buffers, blend, cull, depth) {
      return device.createRenderPipeline({
        layout: "auto",
        vertex: {module: module, entryPoint: vs, buffers: buffers},
        fragment: {module: module, entryPoint: fs,
                   targets: [{format: format, blend: blend || undefined}]},
        primitive: {topology: topology, cullMode: cull || "none"},
        /* The city writes depth so its towers hide each other and the links
           behind them; every other pass is painter-ordered and leaves depth
           alone, so the buffer stays at its cleared 1.0 for them. Links and
           rings are the exception: they test depth without writing it. */
        depthStencil: depth || {format: "depth24plus", depthWriteEnabled: false,
                                depthCompare: "always"},
        multisample: {count: 4}
      });
    }
    this._pipelines = {
      sky: pipeline(modules.sky, "vs_sky", "fs_sky", "triangle-list", [], ALPHA_BLEND),
      cortex: pipeline(modules.cortex, "vs_cortex", "fs_cortex", "triangle-list",
                       layoutOf(CORTEX_SLOTS), ALPHA_BLEND, "back"),
      node: pipeline(modules.node, "vs_node", "fs_node", "triangle-list",
                     layoutOf(NODE_SLOTS, "instance"), ALPHA_BLEND),
      halo: pipeline(modules.node, "vs_node", "fs_halo", "triangle-list",
                     layoutOf(NODE_SLOTS, "instance"), ADD_BLEND),
      /* Links and rings test depth but never write it: in the city a link
         disappears behind a tower, and in the other scene modules the buffer
         is the cleared 1.0, so "less-equal" passes and the painter order
         stands unchanged. */
      edge: pipeline(modules.edge, "vs_edge", "fs_edge", "line-list",
                     layoutOf(EDGE_SLOTS), ALPHA_BLEND, "none",
                     {format: "depth24plus", depthWriteEnabled: false,
                      depthCompare: "less-equal"}),
      ring: pipeline(modules.ring, "vs_ring", "fs_ring", "line-list",
                     layoutOf([{stride: 12, format: "float32x3", loc: 0},
                               {stride: 12, format: "float32x3", loc: 1}]),
                     ALPHA_BLEND, "none",
                     {format: "depth24plus", depthWriteEnabled: false,
                      depthCompare: "less-equal"}),
      ego: pipeline(modules.ego, "vs_ego", "fs_ego", "triangle-list",
                    [{arrayStride: 12, stepMode: "vertex",
                      attributes: [{shaderLocation: 0, offset: 0, format: "float32x3"}]},
                     {arrayStride: 32, stepMode: "instance",
                      attributes: [
                        {shaderLocation: 1, offset: 0, format: "float32x3"},
                        {shaderLocation: 2, offset: 12, format: "float32"},
                        {shaderLocation: 3, offset: 16, format: "float32x3"}]}],
                    ALPHA_BLEND),
      city: pipeline(modules.city, "vs_city", "fs_city", "triangle-list",
                     layoutOf(CITY_SLOTS), ALPHA_BLEND, "none",
                     {format: "depth24plus", depthWriteEnabled: true,
                      depthCompare: "less"})
    };
    var names = ["sky", "cortex", "node", "halo", "edge", "ring", "ego", "city"];
    for (var i = 0; i < names.length; i++) {
      var pipe = this._pipelines[names[i]];
      var size = names[i] === "node" || names[i] === "halo" ? 256 :
                 names[i] === "cortex" ? 256 : names[i] === "edge" ? 192 :
                 names[i] === "city" ? 160 :
                 names[i] === "ring" ? 208 : names[i] === "ego" ? 128 : 32;
      var buffer = device.createBuffer({size: size,
                                        usage: GPUBufferUsage.UNIFORM |
                                               GPUBufferUsage.COPY_DST});
      this._ubos[names[i]] = {
        buffer: buffer,
        group: device.createBindGroup({
          layout: pipe.getBindGroupLayout(0),
          entries: [{binding: 0, resource: {buffer: buffer}}]
        })
      };
    }
  };

  /* Drop buffers whose attribute object has left the scene graph. Three
     replaces whole BufferAttributes on capacity growth, so this is the only
     way to notice that a cached buffer is unreachable. Runs on a cache miss,
     which is rare (a growth step), never per frame. */
  GpuRenderer.prototype._sweep = function (map) {
    var live = new Set();
    this.scene.traverse(function (obj) {
      var geo = obj.geometry;
      if (!geo) return;
      for (var name in geo.attributes) live.add(geo.attributes[name]);
      if (geo.index) live.add(geo.index);
    });
    map.forEach(function (cache, attr) {
      if (live.has(attr)) return;
      cache.buffer.destroy();
      map.delete(attr);
    });
  };

  /* Attribute arrays are owned by the scene layer; a buffer is recreated only
     when the array is replaced (capacity change) and rewritten when Three
     bumps its version. */
  GpuRenderer.prototype._upload = function (attr, usage) {
    if (!attr || !attr.array) return null;
    var map = usage === GPUBufferUsage.INDEX ? this._ibs : this._vbs;
    var cache = map.get(attr);
    var bytes = attr.array.byteLength;
    if (!cache || cache.bytes !== bytes) {
      if (cache) cache.buffer.destroy();
      else this._sweep(map);
      cache = {
        buffer: this.device.createBuffer({
          size: Math.max(4, bytes),
          usage: usage | GPUBufferUsage.COPY_DST
        }),
        version: -1,
        bytes: bytes
      };
      map.set(attr, cache);
    }
    if (cache.version !== attr.version) {
      this.device.queue.writeBuffer(cache.buffer, 0, attr.array);
      cache.version = attr.version;
    }
    return cache.buffer;
  };

  GpuRenderer.prototype._bind = function (name, slots, geo) {
    var pass = this._pass;
    var pipe = this._pipelines[name];
    pass.setPipeline(pipe);
    pass.setBindGroup(0, this._ubos[name].group);
    for (var i = 0; i < slots.length; i++) {
      var attr = geo.getAttribute(slots[i].name);
      var buf = this._upload(attr, GPUBufferUsage.VERTEX);
      if (!buf) return false;
      pass.setVertexBuffer(i, buf);
    }
    return true;
  };

  GpuRenderer.prototype._gpuResize = function () {
    if (!this.gpuReady) return;
    var w = this._resW, h = this._resH;
    if (this._msaa) this._msaa.destroy();
    this._msaa = this.device.createTexture({
      size: [Math.max(1, w), Math.max(1, h)],
      sampleCount: 4,
      format: this._format,
      usage: GPUTextureUsage.RENDER_ATTACHMENT
    });
    this._msaaView = this._msaa.createView();
    /* Depth only exists for the city pass; the other scenes ignore it. */
    if (this._depth) this._depth.destroy();
    this._depth = this.device.createTexture({
      size: [Math.max(1, w), Math.max(1, h)],
      sampleCount: 4,
      format: "depth24plus",
      usage: GPUTextureUsage.RENDER_ATTACHMENT
    });
    this._depthView = this._depth.createView();
  };

  GpuRenderer.prototype.resize = function () {
    if (this._gl) return SK.Renderer.prototype.resize.call(this);
    var w = this.canvas.clientWidth || window.innerWidth;
    var h = this.canvas.clientHeight || (window.innerHeight - 44);
    if (!w || !h) return;
    this.width = w;
    this.height = h;
    var dpr = this.pixelRatio();
    var pw = Math.max(1, Math.round(w * dpr));
    var ph = Math.max(1, Math.round(h * dpr));
    if (this.canvas.width !== pw) this.canvas.width = pw;
    if (this.canvas.height !== ph) this.canvas.height = ph;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.nodeUni.u_px.value = dpr;
    this.nodeUni.u_minpx.value = 1.6 * dpr;
    if (this.skyUni) this.skyUni.u_res.value.set(pw, ph);
    var changed = pw !== this._resW || ph !== this._resH;
    this._resW = pw;
    this._resH = ph;
    if (changed) this._gpuResize();
  };

  GpuRenderer.prototype._packNode = function (out, halo, minMul) {
    var nu = this.nodeUni, cam = this.camera.position;
    var light = nu.u_light.value, tint = nu.u_tint.value;
    var view = this.camera.matrixWorldInverse.elements;
    var proj = this.camera.projectionMatrix.elements;
    out[0] = nu.u_px.value;
    out[1] = nu.u_scale.value;
    out[2] = nu.u_minpx.value;
    out[3] = this.time || 0;
    out[4] = nu.u_fly.value;
    out[5] = nu.u_driftA.value;
    out[6] = nu.u_vol.value;
    out[7] = halo;
    out[8] = nu.u_neural.value;
    out[9] = minMul;
    out[10] = this.haloUni.u_gain.value;
    out[12] = cam.x; out[13] = cam.y; out[14] = cam.z;
    out[16] = light.x; out[17] = light.y; out[18] = light.z;
    for (var i = 0; i < 16; i++) {
      out[20 + i] = view[i];
      out[36 + i] = proj[i];
    }
    out[52] = this._resW;
    out[53] = this._resH;
    out[56] = tint.r; out[57] = tint.g; out[58] = tint.b;
  };

  GpuRenderer.prototype._packEdge = function (out) {
    var eu = this.edgeUni, cam = this.camera.position;
    var view = this.camera.matrixWorldInverse.elements;
    var proj = this.camera.projectionMatrix.elements;
    var targetDist = this.camDistance || cam.length() || 1;
    out[0] = targetDist * 1.15 + this.sceneRadius * 0.95;
    out[1] = eu.u_opacity.value;
    out[2] = eu.u_sweepX.value;
    out[3] = eu.u_sweepAmp.value;
    out[4] = this.time || 0;
    out[5] = eu.u_neural.value;
    out[6] = eu.u_fly.value;
    out[7] = eu.u_driftA.value;
    out[8] = cam.x; out[9] = cam.y; out[10] = cam.z;
    for (var i = 0; i < 16; i++) {
      out[12 + i] = view[i];
      out[28 + i] = proj[i];
    }
  };

  GpuRenderer.prototype._packCortex = function (out) {
    var cu = this.cortexUni;
    var view = this.camera.matrixWorldInverse.elements;
    var proj = this.camera.projectionMatrix.elements;
    out[0] = this.time || 0;
    out[1] = cu.u_fold.value;
    out[2] = cu.u_opacity.value;
    out[4] = cu.u_light.value.x;
    out[5] = cu.u_light.value.y;
    out[6] = cu.u_light.value.z;
    out[8] = cu.u_color.value.r;
    out[9] = cu.u_color.value.g;
    out[10] = cu.u_color.value.b;
    out[12] = cu.u_rim.value.r;
    out[13] = cu.u_rim.value.g;
    out[14] = cu.u_rim.value.b;
    var model = this.cortex.matrixWorld.elements;
    for (var i = 0; i < 16; i++) {
      out[16 + i] = model[i];
      out[32 + i] = view[i];
      out[48 + i] = proj[i];
    }
  };

  GpuRenderer.prototype._packCity = function (out) {
    var cu = this.cityUni;
    var view = this.camera.matrixWorldInverse.elements;
    var proj = this.camera.projectionMatrix.elements;
    out[0] = this.time || 0;
    /* a.y is padding: the lights are the node's own lit fraction (a_spec.y). */
    out[2] = cu.u_finale.value;
    out[3] = cu.u_cityR.value;
    out[4] = cu.u_pitch.value;
    for (var i = 0; i < 16; i++) {
      out[8 + i] = view[i];
      out[24 + i] = proj[i];
    }
  };

  GpuRenderer.prototype._packSky = function (out) {
    var cam = this.camera.position;
    var clen = Math.max(cam.length(), 1);
    out[0] = this.time || 0;
    out[1] = this.skyUni.u_mode.value;
    out[4] = Math.atan2(cam.x, cam.z) * 0.16;
    out[5] = Math.asin(cam.y / clen) * 0.24;
    out[6] = this._resW;
    out[7] = this._resH;
  };

  /* Ego instances are read back out of the Three InstancedMesh the scene
     layer already packs, so the finale pulse and colours stay in one place. */
  GpuRenderer.prototype._readEgo = function (out) {
    var mesh = this.egoMesh;
    if (!mesh || !mesh.instanceMatrix || !mesh.instanceColor) return 0;
    var count = Math.min(mesh.count, 120);
    var mat = mesh.instanceMatrix.array, col = mesh.instanceColor.array;
    for (var i = 0; i < count; i++) {
      var m = i * 16, c = i * 3;
      out[i * 8] = mat[m + 12];
      out[i * 8 + 1] = mat[m + 13];
      out[i * 8 + 2] = mat[m + 14];
      out[i * 8 + 3] = mat[m] || 1;
      out[i * 8 + 4] = col[c];
      out[i * 8 + 5] = col[c + 1];
      out[i * 8 + 6] = col[c + 2];
      out[i * 8 + 7] = 0;
    }
    return count;
  };

  GpuRenderer.prototype.render = function () {
    if (this._gl) return SK.Renderer.prototype.render.call(this);
    if (!this.gpuReady || this.contextLost || this._disposed) return;
    if (!this.live || !this.pgeo.getAttribute("position")) return;

    var cam = this.camera;
    cam.updateMatrixWorld();
    cam.matrixWorldInverse.copy(cam.matrixWorld).invert();

    /* The same per-frame uniform updates the WebGL renderer performs, so the
       fold growth, the star drift and the sweep stay in lockstep. */
    this.nodeUni.u_time.value = this.time || 0;
    this.edgeUni.u_time.value = this.time || 0;
    if (this.cortex && this.cortex.visible) {
      var live = this.live, seen = 0;
      for (var s = 0; s < live.n; s++) if (live.revealed[s]) seen++;
      this.cortexUni.u_fold.value = live.n ? seen / live.n : 0;
      this.cortexUni.u_time.value = this.time || 0;
    }
    if (this.sky && this.sky.visible) this.skyUni.u_time.value = this.time || 0;
    this.edgeUni.u_cull.value = (this.camDistance || cam.position.length() || 1) * 1.15
                              + this.sceneRadius * 0.95;
    this.edgeUni.u_cam.value.copy(cam.position);
    this.nodeUni.u_cam.value.copy(cam.position);

    this._packSky(this._uSky);
    this._packNode(this._uNode, 1, 0.7);
    this._packNode(this._uHalo, this.haloUni.u_halo.value, 1.2);
    this._packEdge(this._uEdge);
    this.device.queue.writeBuffer(this._ubos.sky.buffer, 0, this._uSky);
    this.device.queue.writeBuffer(this._ubos.node.buffer, 0, this._uNode);
    this.device.queue.writeBuffer(this._ubos.halo.buffer, 0, this._uHalo);
    this.device.queue.writeBuffer(this._ubos.edge.buffer, 0, this._uEdge);

    var city = this.cityMesh && this.cityMesh.visible && this.cityGeo &&
               this.cityGeo.getAttribute("position");
    if (city) {
      this._packCity(this._uCity);
      this.device.queue.writeBuffer(this._ubos.city.buffer, 0, this._uCity);
    }

    if (this.cortex && this.cortex.visible) {
      this.cortex.updateMatrixWorld(true);
      this._packCortex(this._uCortex);
      this.device.queue.writeBuffer(this._ubos.cortex.buffer, 0, this._uCortex);
    }

    var encoder = this.device.createCommandEncoder();
    var target = this._context.getCurrentTexture().createView();
    var pass = encoder.beginRenderPass({
      colorAttachments: [{
        view: this._msaaView,
        resolveTarget: target,
        clearValue: this._clear,
        loadOp: "clear",
        storeOp: "discard"
      }],
      depthStencilAttachment: this._depthView ? {
        view: this._depthView,
        depthClearValue: 1,
        depthLoadOp: "clear",
        depthStoreOp: "discard"
      } : undefined
    });
    this._pass = pass;

    if (this.sky && this.sky.visible) {
      pass.setPipeline(this._pipelines.sky);
      pass.setBindGroup(0, this._ubos.sky.group);
      pass.draw(3);
    }

    /* The sky is a depth-ignoring full-screen quad, so the city has to come
       after it and before every additive pass: it is the only writer of the
       depth buffer, and the later passes compare "always" so they paint over
       it as before. */
    if (city) {
      var cityGeo = this.cityGeo;
      var cityIdx = cityGeo.index;
      var cbuf = cityIdx && this._upload(cityIdx, GPUBufferUsage.INDEX);
      if (cbuf && this._bind("city", CITY_SLOTS, cityGeo)) {
        pass.setIndexBuffer(cbuf, "uint32");
        pass.drawIndexed(cityIdx.count);
      }
    }

    if (this.ringMesh && this.ringMesh.visible && this.ringMesh.geometry) {
      var ringGeo = this.ringMesh.geometry;
      var rpos = ringGeo.getAttribute("position");
      if (rpos && this._bind("ring", [{name: "position"}, {name: "color"}], ringGeo)) {
        this.ringMesh.updateMatrixWorld(true);
        var rview = cam.matrixWorldInverse.elements;
        var rproj = cam.projectionMatrix.elements;
        var rmodel = this.ringMesh.matrixWorld.elements;
        this._uRing[0] = (this.ringMesh.material && this.ringMesh.material.opacity) || 0.18;
        for (var ri = 0; ri < 16; ri++) {
          this._uRing[4 + ri] = rmodel[ri];
          this._uRing[20 + ri] = rview[ri];
          this._uRing[36 + ri] = rproj[ri];
        }
        this.device.queue.writeBuffer(this._ubos.ring.buffer, 0, this._uRing);
        pass.draw(rpos.count);
      }
    }

    if (this.lines && this.lines.geometry) {
      var edgeGeo = this.lines.geometry;
      var epos = edgeGeo.getAttribute("position");
      if (epos && this._bind("edge", [
        {name: "position"}, {name: "color"}, {name: "a_base"}, {name: "a_dim"},
        {name: "a_birth"}, {name: "a_t"}, {name: "a_seed"}
      ], edgeGeo)) {
        if (this.lodReady && this.lodVersion === this.live.version && edgeGeo.index) {
          var ratio = (this.camDistance || 1) / Math.max(this.sceneRadius, 1);
          var fraction = ratio > 2.4 ? 0.3 : (ratio > 1.5 ? 0.6 : 1);
          var idx = this._upload(edgeGeo.index, GPUBufferUsage.INDEX);
          if (idx) {
            var segs = Math.max(1, Math.floor(this.live.m * fraction));
            pass.setIndexBuffer(idx, "uint32");
            pass.drawIndexed(segs * 2);
          }
        } else {
          pass.draw(this._drawEdges * 2);
        }
      }
    }

    if (this.cortex && this.cortex.visible && this.cortex.geometry) {
      var cgeo = this.cortex.geometry;
      var cidx = cgeo.index;
      if (cidx && this._bind("cortex", [
        {name: "position"}, {name: "normal"}, {name: "a_flat"},
        {name: "a_flatN"}, {name: "a_dev"}, {name: "a_fold"}
      ], cgeo)) {
        var ibuf = this._upload(cidx, GPUBufferUsage.INDEX);
        if (ibuf) {
          pass.setIndexBuffer(ibuf, "uint16");
          pass.drawIndexed(cidx.count);
        }
      }
    }

    var count = this.live.n;
    if (count && this.cloud.visible && this._bind("node", NODE_SLOTS, this.pgeo)) {
      pass.draw(6, count);
    }
    if (count && this.halo.visible && this._bind("halo", NODE_SLOTS, this.pgeo)) {
      pass.draw(6, count);
    }

    if (this.egoMesh && this.egoMesh.visible && this.egoMesh.count) {
      var egoGeo = this.egoMesh.geometry;
      var instances = this._readEgo(this._uEgoData);
      if (instances && egoGeo.index && this._bind("ego",
          [{name: "position"}], egoGeo)) {
        if (!this._egoBuf || this._egoBufBytes < instances * 32) {
          if (this._egoBuf) this._egoBuf.destroy();
          this._egoBufBytes = 120 * 32;
          this._egoBuf = this.device.createBuffer({
            size: this._egoBufBytes,
            usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST
          });
        }
        this.device.queue.writeBuffer(this._egoBuf, 0, this._uEgoData,
                                      0, instances * 8);
        pass.setVertexBuffer(1, this._egoBuf);
        var view = cam.matrixWorldInverse.elements;
        var proj = cam.projectionMatrix.elements;
        for (var ei = 0; ei < 16; ei++) {
          this._uEgo[ei] = view[ei];
          this._uEgo[16 + ei] = proj[ei];
        }
        this.device.queue.writeBuffer(this._ubos.ego.buffer, 0, this._uEgo);
        var eidx = this._upload(egoGeo.index, GPUBufferUsage.INDEX);
        if (eidx) {
          pass.setIndexBuffer(eidx, "uint16");
          pass.drawIndexed(egoGeo.index.count, instances);
        }
      }
    }

    pass.end();
    this.device.queue.submit([encoder.finish()]);
  };

  GpuRenderer.prototype.dispose = function () {
    this._disposed = true;
    if (this._gl) return SK.Renderer.prototype.dispose.call(this);
    this._disposeScene();
    this._vbs.forEach(function (c) { c.buffer.destroy(); });
    this._vbs.clear();
    this._ibs.forEach(function (c) { c.buffer.destroy(); });
    this._ibs.clear();
    for (var name in this._ubos) {
      if (this._ubos[name]) this._ubos[name].buffer.destroy();
    }
    this._ubos = {};
    if (this._egoBuf) {
      this._egoBuf.destroy();
      this._egoBuf = null;
    }
    if (this._msaa) {
      this._msaa.destroy();
      this._msaa = null;
    }
    if (this._depth) {
      this._depth.destroy();
      this._depth = null;
      this._depthView = null;
    }
    if (this._context) {
      try { this._context.unconfigure(); } catch (ignored) {}
    }
    this.gpuReady = false;
  };

  /* Scratch uniform blocks: one per pipeline, rewritten each frame. */
  GpuRenderer.prototype._initScratch = function () {
    this._uSky = new Float32Array(8);
    this._uNode = new Float32Array(64);
    this._uHalo = new Float32Array(64);
    this._uEdge = new Float32Array(44);
    this._uCortex = new Float32Array(64);
    this._uRing = new Float32Array(52);
    this._uEgo = new Float32Array(32);
    this._uCity = new Float32Array(40);
    this._uEgoData = new Float32Array(120 * 8);
    this._egoBufBytes = 0;
  };

  SK.GpuRenderer = GpuRenderer;
  SK.hasWebGPU = GpuRenderer.supported;
})(window.SK = window.SK || {});
