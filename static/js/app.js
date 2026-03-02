/* ══════════════════════════════════════════════════════════════
   ConcreteIQ — Premium Interactions Module
   ══════════════════════════════════════════════════════════════ */

(function() {
    'use strict';

    /* ── Toast Notification System ── */
    var TOAST_ICONS = {
        success: '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        error:   '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        info:    '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        warning: '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.268 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>',
    };

    var TOAST_TITLES = {
        success: 'Success',
        error: 'Error',
        info: 'Info',
        warning: 'Warning',
    };

    function escapeHtml(str) {
        if (!str) return '';
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function getToastContainer() {
        var container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'fixed bottom-4 right-4 z-50 space-y-3';
            document.body.appendChild(container);
        }
        return container;
    }

    function createToast(type, message, duration) {
        duration = duration || 4000;
        var container = getToastContainer();

        var toast = document.createElement('div');
        toast.className = 'toast toast-' + type;
        toast.innerHTML =
            '<div class="toast-accent"></div>' +
            TOAST_ICONS[type] +
            '<div class="toast-body">' +
                '<div class="toast-title">' + TOAST_TITLES[type] + '</div>' +
                '<div class="toast-message">' + message + '</div>' +
            '</div>' +
            '<button class="toast-close" aria-label="Close">' +
                '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>' +
            '</button>' +
            '<div class="toast-progress"><div class="toast-progress-bar" style="animation-duration: ' + duration + 'ms"></div></div>';

        toast.querySelector('.toast-close').addEventListener('click', function() {
            dismissToast(toast);
        });

        container.appendChild(toast);
        Sound.play(type === 'info' || type === 'warning' ? 'notification' : type);

        setTimeout(function() { dismissToast(toast); }, duration);
        return toast;
    }

    function dismissToast(toast) {
        if (toast.classList.contains('toast-exit')) return;
        toast.classList.add('toast-exit');
        setTimeout(function() { toast.remove(); }, 300);
    }

    window.Toast = {
        success: function(msg, dur) { return createToast('success', msg, dur); },
        error:   function(msg, dur) { return createToast('error', msg, dur); },
        info:    function(msg, dur) { return createToast('info', msg, dur); },
        warning: function(msg, dur) { return createToast('warning', msg, dur); },
    };


    /* ── Premium Confirm Dialog ── */
    window.Confirm = {
        show: function(opts) {
            var title = opts.title || 'Confirm';
            var message = opts.message || 'Are you sure?';
            var confirmText = opts.confirmText || 'Confirm';
            var cancelText = opts.cancelText || 'Cancel';
            var danger = opts.danger || false;
            var onConfirm = opts.onConfirm || function(){};
            var onCancel = opts.onCancel || function(){};

            var backdrop = document.createElement('div');
            backdrop.className = 'confirm-backdrop';

            var dialog = document.createElement('div');
            dialog.className = 'confirm-dialog';
            dialog.innerHTML =
                '<h3>' + title + '</h3>' +
                '<p>' + message + '</p>' +
                '<div class="confirm-actions">' +
                    '<button class="btn-v4 btn-v4-secondary confirm-cancel">' + cancelText + '</button>' +
                    '<button class="btn-v4 ' + (danger ? 'btn-v4-danger' : 'btn-v4-primary') + ' confirm-ok">' + confirmText + '</button>' +
                '</div>';

            function close() {
                backdrop.remove();
                dialog.remove();
            }

            dialog.querySelector('.confirm-cancel').addEventListener('click', function() {
                close(); onCancel();
            });
            dialog.querySelector('.confirm-ok').addEventListener('click', function() {
                close(); onConfirm();
            });
            backdrop.addEventListener('click', function() {
                close(); onCancel();
            });

            document.body.appendChild(backdrop);
            document.body.appendChild(dialog);
            dialog.querySelector('.confirm-ok').focus();
        }
    };


    /* ── Animated Number Counters ── */
    function animateCounter(el) {
        var target = parseFloat(el.getAttribute('data-count-to')) || 0;
        var prefix = el.getAttribute('data-count-prefix') || '';
        var suffix = el.getAttribute('data-count-suffix') || '';
        var decimals = parseInt(el.getAttribute('data-count-decimals')) || 0;
        var duration = parseInt(el.getAttribute('data-count-duration')) || 1200;
        var startTime = null;

        function easeOutExpo(t) {
            return t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
        }

        function formatNumber(n) {
            var parts = n.toFixed(decimals).split('.');
            parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
            return prefix + parts.join('.') + suffix;
        }

        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            var progress = Math.min((timestamp - startTime) / duration, 1);
            var easedProgress = easeOutExpo(progress);
            var current = target * easedProgress;
            el.textContent = formatNumber(current);

            if (progress < 1) {
                requestAnimationFrame(step);
            } else {
                el.textContent = formatNumber(target);
                el.classList.add('pop');
                setTimeout(function() { el.classList.remove('pop'); }, 300);
            }
        }

        requestAnimationFrame(step);
    }

    function initCounters() {
        var counters = document.querySelectorAll('[data-count-to]');
        if (!counters.length) return;

        if ('IntersectionObserver' in window) {
            var observer = new IntersectionObserver(function(entries) {
                entries.forEach(function(entry) {
                    if (entry.isIntersecting) {
                        animateCounter(entry.target);
                        observer.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.3 });

            counters.forEach(function(el) { observer.observe(el); });
        } else {
            counters.forEach(animateCounter);
        }
    }


    /* ── Button Ripple Effect ── */
    function initRipples() {
        document.addEventListener('click', function(e) {
            var btn = e.target.closest('.btn-primary, .btn-secondary, .btn-danger, .btn-v4-primary, .btn-v4-secondary, .btn-v4-danger');
            if (!btn) return;

            var rect = btn.getBoundingClientRect();
            var size = Math.max(rect.width, rect.height);
            var x = e.clientX - rect.left - size / 2;
            var y = e.clientY - rect.top - size / 2;

            var ripple = document.createElement('span');
            ripple.className = 'ripple';
            ripple.style.width = ripple.style.height = size + 'px';
            ripple.style.left = x + 'px';
            ripple.style.top = y + 'px';

            btn.appendChild(ripple);
            setTimeout(function() { ripple.remove(); }, 600);
        });
    }


    /* ── Premium Sound Engine v3 ── */
    /* High-end notification sounds with rich harmonics, stereo imaging, and lush reverb */
    var audioCtx = null;
    function getAudioCtx() {
        if (!audioCtx) {
            try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
            catch(e) { return null; }
        }
        return audioCtx;
    }

    var Sound = {
        enabled: localStorage.getItem('concreteiq_sounds') !== 'false',
        _nodes: [],  /* Track nodes for cleanup */

        /* ── Building Blocks ── */

        /* Create a lush stereo reverb impulse response */
        _reverb: function(ctx, decay, length) {
            var rate = ctx.sampleRate;
            var len = Math.floor(rate * length);
            var impulse = ctx.createBuffer(2, len, rate);
            for (var ch = 0; ch < 2; ch++) {
                var data = impulse.getChannelData(ch);
                for (var i = 0; i < len; i++) {
                    /* Stereo decorrelation + exponential decay */
                    data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
                }
            }
            var conv = ctx.createConvolver();
            conv.buffer = impulse;
            return conv;
        },

        /* Soft waveshaper for analog warmth */
        _saturator: function(ctx) {
            var ws = ctx.createWaveShaper();
            var n = 256, curve = new Float32Array(n);
            for (var i = 0; i < n; i++) {
                var x = (i * 2) / n - 1;
                curve[i] = (Math.PI + 3.5) * x / (Math.PI + 3.5 * Math.abs(x));
            }
            ws.curve = curve;
            ws.oversample = '2x';
            return ws;
        },

        /* Create master signal chain: saturation → compressor → reverb send */
        _chain: function(ctx, reverbDecay, reverbLen, reverbMix) {
            /* Reverb with pre-delay and high-cut */
            var reverb = this._reverb(ctx, reverbDecay || 2.8, reverbLen || 1.2);
            var reverbGain = ctx.createGain();
            reverbGain.gain.value = reverbMix || 0.22;
            var reverbHp = ctx.createBiquadFilter();
            reverbHp.type = 'highpass'; reverbHp.frequency.value = 400;
            var reverbLp = ctx.createBiquadFilter();
            reverbLp.type = 'lowpass'; reverbLp.frequency.value = 6000;
            reverbHp.connect(reverb); reverb.connect(reverbLp);
            reverbLp.connect(reverbGain); reverbGain.connect(ctx.destination);

            /* Subtle analog warmth */
            var sat = this._saturator(ctx);

            /* Gentle master compression */
            var comp = ctx.createDynamicsCompressor();
            comp.threshold.value = -18; comp.ratio.value = 4; comp.knee.value = 12;
            comp.attack.value = 0.002; comp.release.value = 0.2;

            sat.connect(comp);
            comp.connect(ctx.destination);
            comp.connect(reverbHp);

            this._nodes.push(sat, comp, reverb, reverbGain, reverbHp, reverbLp);
            return sat;  /* Entry point for all sounds */
        },

        /* Voice: fundamental + harmonics with natural decay, optional stereo pan */
        _voice: function(ctx, freq, vol, start, dur, dest, pan) {
            var g = ctx.createGain();
            if (typeof pan === 'number') {
                var p = ctx.createStereoPanner();
                p.pan.value = pan;
                g.connect(p); p.connect(dest);
                this._nodes.push(p);
            } else {
                g.connect(dest);
            }

            /* Fundamental with smooth envelope */
            var o = ctx.createOscillator();
            o.type = 'sine'; o.frequency.value = freq;
            o.connect(g);
            g.gain.setValueAtTime(0, start);
            g.gain.linearRampToValueAtTime(vol, start + 0.008);
            g.gain.setTargetAtTime(vol * 0.6, start + 0.008, dur * 0.15);
            g.gain.setTargetAtTime(0.0001, start + dur * 0.5, dur * 0.25);
            o.start(start); o.stop(start + dur + 0.1);

            this._nodes.push(o, g);
            return g;
        },

        /* Crystal: bright layered tone with detuned chorus & inharmonic partials */
        _crystal: function(ctx, freq, vol, start, dur, dest, pan) {
            /* Fundamental */
            this._voice(ctx, freq, vol, start, dur, dest, pan);
            /* Chorus detuning for stereo width */
            this._voice(ctx, freq * 1.002, vol * 0.3, start, dur * 0.9, dest, (pan || 0) - 0.3);
            this._voice(ctx, freq * 0.998, vol * 0.3, start, dur * 0.9, dest, (pan || 0) + 0.3);
            /* 2nd harmonic (slightly sharp for bell character) */
            this._voice(ctx, freq * 2.01, vol * 0.18, start + 0.003, dur * 0.55, dest, pan);
            /* 3rd harmonic (adds brightness) */
            this._voice(ctx, freq * 3.005, vol * 0.06, start + 0.005, dur * 0.35, dest, pan);
            /* Sub octave for warmth */
            this._voice(ctx, freq * 0.5, vol * 0.12, start, dur * 0.7, dest, 0);
        },

        /* Chime: pure tone with long shimmering tail */
        _chime: function(ctx, freq, vol, start, dur, dest, pan) {
            this._voice(ctx, freq, vol, start, dur, dest, pan);
            /* Detuned pair for shimmer */
            this._voice(ctx, freq * 1.001, vol * 0.25, start, dur * 1.1, dest, -0.4);
            this._voice(ctx, freq * 0.999, vol * 0.25, start, dur * 1.1, dest, 0.4);
            /* Octave above, very soft for air */
            this._voice(ctx, freq * 2, vol * 0.08, start + 0.01, dur * 0.5, dest, pan);
        },

        /* Noise burst: filtered noise for transient texture (like a mallet striking) */
        _transient: function(ctx, freq, vol, start, dur, dest) {
            var bufSize = Math.floor(ctx.sampleRate * dur);
            var buf = ctx.createBuffer(1, bufSize, ctx.sampleRate);
            var data = buf.getChannelData(0);
            for (var i = 0; i < bufSize; i++) {
                data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / bufSize, 8);
            }
            var src = ctx.createBufferSource();
            src.buffer = buf;
            var bp = ctx.createBiquadFilter();
            bp.type = 'bandpass'; bp.frequency.value = freq; bp.Q.value = 2;
            var g = ctx.createGain(); g.gain.value = vol;
            src.connect(bp); bp.connect(g); g.connect(dest);
            src.start(start);
            this._nodes.push(src, bp, g);
        },

        /* ── Sound Playback ── */

        play: function(type) {
            if (!this.enabled) return;
            var ctx = getAudioCtx();
            if (!ctx) return;

            this._nodes = [];
            var now = ctx.currentTime;
            var bus;

            switch(type) {

                case 'click':
                    /* Subtle tactile pop — like tapping quality glass */
                    bus = this._chain(ctx, 1.5, 0.3, 0.06);
                    this._transient(ctx, 3000, 0.02, now, 0.025, bus);
                    this._voice(ctx, 900, 0.012, now, 0.04, bus, 0);
                    this._voice(ctx, 450, 0.006, now, 0.05, bus, 0);
                    break;

                case 'success':
                    /* Ascending crystal arpeggio — C5→E5→G5→C6 with sparkle tail */
                    /* Like a luxury brand confirmation / Stripe-inspired */
                    bus = this._chain(ctx, 3.0, 1.5, 0.25);
                    this._transient(ctx, 4000, 0.008, now, 0.02, bus);
                    this._crystal(ctx, 523,  0.030, now,        0.55, bus, -0.2);  /* C5 */
                    this._crystal(ctx, 659,  0.032, now + 0.09, 0.50, bus,  0.0);  /* E5 */
                    this._crystal(ctx, 784,  0.034, now + 0.18, 0.50, bus,  0.2);  /* G5 */
                    this._chime(ctx,   1047, 0.028, now + 0.30, 0.90, bus,  0.0);  /* C6 — hold */
                    /* High sparkle tail */
                    this._voice(ctx, 2093, 0.004, now + 0.35, 0.6, bus, 0.3);
                    this._voice(ctx, 1568, 0.003, now + 0.38, 0.5, bus, -0.3);
                    break;

                case 'error':
                    /* Gentle descending minor 2nd — warm but clear something's wrong */
                    /* E4 → Eb4, soft and padded */
                    bus = this._chain(ctx, 2.2, 0.8, 0.18);
                    this._crystal(ctx, 330, 0.030, now,        0.28, bus, -0.1);   /* E4  */
                    this._crystal(ctx, 311, 0.034, now + 0.22, 0.35, bus,  0.1);   /* Eb4 */
                    /* Subtle warm body */
                    this._voice(ctx, 165, 0.010, now, 0.45, bus, 0);
                    break;

                case 'notification':
                    /* Two-tone hotel chime — G5 → D6 (perfect 5th) */
                    /* Clean, warm, unmistakably a notification */
                    bus = this._chain(ctx, 3.2, 1.4, 0.28);
                    this._transient(ctx, 5000, 0.006, now, 0.015, bus);
                    this._chime(ctx, 784,  0.032, now,        0.65, bus, -0.15);   /* G5 */
                    this._chime(ctx, 1175, 0.034, now + 0.18, 0.85, bus,  0.15);   /* D6 */
                    /* Warm sub presence */
                    this._voice(ctx, 392, 0.008, now + 0.05, 0.50, bus, 0);
                    break;

                case 'money':
                    /* Cash register: metallic strike → rich major chord ring-out */
                    /* Weighty and satisfying — you just got paid */
                    bus = this._chain(ctx, 3.5, 1.8, 0.30);
                    /* Coin strike transient */
                    this._transient(ctx, 6000, 0.015, now, 0.03, bus);
                    this._transient(ctx, 3000, 0.010, now, 0.04, bus);
                    /* Rich Cmaj7 chord ring — staggered for shimmer */
                    this._crystal(ctx, 1047, 0.026, now + 0.04, 0.85, bus, -0.25); /* C6 */
                    this._crystal(ctx, 1319, 0.024, now + 0.08, 0.75, bus,  0.0);  /* E6 */
                    this._crystal(ctx, 1568, 0.022, now + 0.12, 0.70, bus,  0.25); /* G6 */
                    this._chime(ctx,   1976, 0.016, now + 0.16, 0.65, bus,  0.0);  /* B6 */
                    /* Deep warm foundation */
                    this._voice(ctx, 262, 0.012, now + 0.04, 0.70, bus, 0);        /* C4 sub */
                    this._voice(ctx, 523, 0.008, now + 0.04, 0.55, bus, 0);        /* C5 body */
                    /* Final sparkle */
                    this._voice(ctx, 3136, 0.003, now + 0.20, 0.50, bus, 0.4);
                    this._voice(ctx, 2637, 0.003, now + 0.22, 0.45, bus, -0.4);
                    break;

                case 'alert':
                    /* Attention pulse: descending minor 3rd × 2 with urgency */
                    /* A5 → F5, repeat softer — clear but not harsh */
                    bus = this._chain(ctx, 2.0, 0.8, 0.15);
                    this._transient(ctx, 4000, 0.008, now, 0.02, bus);
                    /* First phrase */
                    this._crystal(ctx, 880, 0.034, now,        0.16, bus, -0.1);   /* A5 */
                    this._crystal(ctx, 698, 0.036, now + 0.14, 0.22, bus,  0.1);   /* F5 */
                    /* Softer echo */
                    this._crystal(ctx, 880, 0.018, now + 0.42, 0.14, bus,  0.1);
                    this._crystal(ctx, 698, 0.022, now + 0.54, 0.20, bus, -0.1);
                    /* Sub weight */
                    this._voice(ctx, 349, 0.008, now + 0.14, 0.35, bus, 0);
                    break;
            }

            /* Cleanup all audio nodes after playback completes */
            var nodes = this._nodes;
            setTimeout(function() {
                for (var i = 0; i < nodes.length; i++) {
                    try { nodes[i].disconnect(); } catch(e) {}
                }
            }, 3000);
        },

        toggle: function() {
            this.enabled = !this.enabled;
            localStorage.setItem('concreteiq_sounds', this.enabled);
            return this.enabled;
        }
    };

    window.Sound = Sound;


    /* ── Progress Bar Animation ── */
    function initProgressBars() {
        var bars = document.querySelectorAll('[data-progress-to]');
        if (!bars.length) return;

        if ('IntersectionObserver' in window) {
            var observer = new IntersectionObserver(function(entries) {
                entries.forEach(function(entry) {
                    if (entry.isIntersecting) {
                        var target = entry.target.getAttribute('data-progress-to');
                        entry.target.style.width = target + '%';
                        observer.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.1 });

            bars.forEach(function(el) {
                el.style.width = '0%';
                observer.observe(el);
            });
        } else {
            bars.forEach(function(el) {
                el.style.width = el.getAttribute('data-progress-to') + '%';
            });
        }
    }


    /* ── Live Notification Polling ── */
    // Maps notification types to sound + toast style
    var NOTIF_SOUND_MAP = {
        // Money events — ka-ching
        payment_received: 'money',
        deposit_received: 'money',
        // Urgent events — double-beep alert
        quote_declined: 'alert',
        amendment_declined: 'alert',
        email_bounced: 'alert',
        invoice_overdue: 'alert',
        payment_reminder_firm: 'alert',
        payment_reminder_final: 'alert',
        // Positive events — success chime
        quote_accepted: 'success',
        amendment_accepted: 'success',
        job_completed: 'success',
        // Inbound SMS — alert (customer replied!)
        inbound_sms: 'alert',
        // Email engagement — notification chirp
        email_opened: 'notification',
        email_clicked: 'success',
        // Follow-up nudges — notification chirp
        quote_followup: 'notification',
        // Everything else — notification chirp
    };

    var NOTIF_TOAST_MAP = {
        payment_received: 'success',
        deposit_received: 'success',
        quote_accepted: 'success',
        amendment_accepted: 'success',
        job_completed: 'success',
        quote_declined: 'warning',
        amendment_declined: 'warning',
        quote_followup: 'info',
        email_bounced: 'error',
        invoice_overdue: 'warning',
        payment_reminder_firm: 'warning',
        payment_reminder_final: 'error',
        // Inbound SMS — info toast (customer replied)
        inbound_sms: 'info',
        // Email engagement
        email_opened: 'info',
        email_clicked: 'success',
    };

    var NotificationPoller = {
        lastCheck: null,
        interval: null,
        badgeEl: null,
        _polling: false,
        POLL_INTERVAL: 10000,       // 10 seconds (was 30s)
        POLL_INTERVAL_BG: 30000,    // 30 seconds when tab is hidden

        start: function() {
            // Only poll on authenticated pages (not portal/login)
            if (document.querySelector('[data-no-poll]') || window.location.pathname.startsWith('/p/') || window.location.pathname === '/login') {
                return;
            }

            this.lastCheck = new Date().toISOString();
            this.badgeEl = document.getElementById('notification-badge');

            var self = this;

            // Immediate first poll (after 1s to let page settle)
            setTimeout(function() { self.poll(); }, 1000);

            // Start regular polling
            this._startInterval();

            // Poll immediately when tab becomes visible again
            document.addEventListener('visibilitychange', function() {
                if (document.visibilityState === 'visible') {
                    self.poll();
                    self._startInterval();  // Reset to fast interval
                } else {
                    // Slow down when tab is hidden (save resources)
                    self._startInterval(self.POLL_INTERVAL_BG);
                }
            });
        },

        _startInterval: function(ms) {
            var self = this;
            if (this.interval) clearInterval(this.interval);
            this.interval = setInterval(function() { self.poll(); }, ms || this.POLL_INTERVAL);
        },

        poll: function() {
            if (this._polling) return;  // Prevent overlapping requests
            this._polling = true;

            var self = this;
            var url = '/notifications/api/poll';
            if (this.lastCheck) {
                url += '?since=' + encodeURIComponent(this.lastCheck);
            }

            fetch(url)
                .then(function(r) { return r.ok ? r.json() : null; })
                .then(function(data) {
                    if (!data) return;

                    // Update badge count and styling
                    if (self.badgeEl) {
                        if (data.unread_count > 0) {
                            self.badgeEl.textContent = data.unread_count > 99 ? '99+' : data.unread_count;
                            self.badgeEl.style.display = '';
                            self.badgeEl.style.background = '#ef4444';
                            self.badgeEl.style.color = '#fff';
                        } else {
                            self.badgeEl.textContent = '0';
                            self.badgeEl.style.background = 'rgba(255,255,255,0.15)';
                            self.badgeEl.style.color = 'rgba(255,255,255,0.35)';
                            self.badgeEl.style.display = '';
                        }
                    }

                    // Show toast for each new notification
                    if (data.notifications && data.notifications.length > 0) {
                        data.notifications.forEach(function(n) {
                            var toastType = NOTIF_TOAST_MAP[n.type] || 'info';
                            var soundType = NOTIF_SOUND_MAP[n.type] || 'notification';

                            // Create toast without default sound
                            var container = getToastContainer();
                            var toast = document.createElement('div');
                            toast.className = 'toast toast-' + toastType;
                            toast.innerHTML =
                                '<div class="toast-accent"></div>' +
                                TOAST_ICONS[toastType] +
                                '<div class="toast-body">' +
                                    '<div class="toast-title">' + escapeHtml(n.title) + '</div>' +
                                    '<div class="toast-message">' + escapeHtml(n.message) + '</div>' +
                                '</div>' +
                                '<button class="toast-close" aria-label="Close">' +
                                    '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>' +
                                '</button>' +
                                '<div class="toast-progress"><div class="toast-progress-bar" style="animation-duration: 6000ms"></div></div>';

                            toast.querySelector('.toast-close').addEventListener('click', function() {
                                dismissToast(toast);
                            });

                            container.appendChild(toast);
                            Sound.play(soundType);
                            setTimeout(function() { dismissToast(toast); }, 6000);
                        });

                        // Update lastCheck to latest notification time
                        var last = data.notifications[data.notifications.length - 1];
                        if (last.created_at) {
                            self.lastCheck = last.created_at;
                        }
                    }

                    // Sync recent items to the Alpine dropdown (if open)
                    if (data.recent) {
                        var dropdownEl = document.querySelector('[x-data*="notifOpen"]');
                        if (dropdownEl && dropdownEl.__x) {
                            dropdownEl.__x.$data.items = data.recent;
                        }
                    }
                })
                .catch(function() { /* silent fail — network hiccup */ })
                .finally(function() { self._polling = false; });
        },

        stop: function() {
            if (this.interval) {
                clearInterval(this.interval);
                this.interval = null;
            }
        }
    };

    window.NotificationPoller = NotificationPoller;


    /* ── Page Load Orchestration ── */
    document.addEventListener('DOMContentLoaded', function() {
        initCounters();
        initRipples();
        initProgressBars();
        NotificationPoller.start();

        // Resume AudioContext on first user interaction (browser autoplay policy)
        document.addEventListener('click', function resumeAudio() {
            var ctx = getAudioCtx();
            if (ctx && ctx.state === 'suspended') ctx.resume();
            document.removeEventListener('click', resumeAudio);
        }, { once: true });
    });

})();
