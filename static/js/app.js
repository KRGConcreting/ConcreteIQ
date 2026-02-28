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
            var btn = e.target.closest('.btn-primary, .btn-secondary, .btn-danger');
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


    /* ── Sound Effects Module ── */
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

        _note: function(ctx, freq, type, gainVal, start, dur, dest) {
            var o = ctx.createOscillator();
            var g = ctx.createGain();
            o.connect(g); g.connect(dest || ctx.destination);
            o.type = type || 'sine';
            o.frequency.setValueAtTime(freq, start);
            g.gain.setValueAtTime(0, start);
            g.gain.linearRampToValueAtTime(gainVal, start + 0.008);
            g.gain.setValueAtTime(gainVal, start + dur * 0.6);
            g.gain.exponentialRampToValueAtTime(0.001, start + dur);
            o.start(start); o.stop(start + dur + 0.01);
            return o;
        },

        play: function(type) {
            if (!this.enabled) return;
            var ctx = getAudioCtx();
            if (!ctx) return;
            var self = this;
            var now = ctx.currentTime;

            switch(type) {
                case 'click':
                    // Soft tap — short sine pop with harmonic
                    self._note(ctx, 600, 'sine', 0.025, now, 0.06);
                    self._note(ctx, 1200, 'sine', 0.008, now, 0.04);
                    break;

                case 'success':
                    // Warm major chord arpeggio: C5 → E5 → G5 → C6
                    self._note(ctx, 523, 'sine', 0.035, now, 0.28);
                    self._note(ctx, 523, 'triangle', 0.015, now, 0.28);
                    self._note(ctx, 659, 'sine', 0.035, now + 0.1, 0.25);
                    self._note(ctx, 784, 'sine', 0.035, now + 0.2, 0.25);
                    self._note(ctx, 784, 'triangle', 0.012, now + 0.2, 0.25);
                    self._note(ctx, 1047, 'sine', 0.03, now + 0.3, 0.35);
                    self._note(ctx, 1047, 'triangle', 0.01, now + 0.3, 0.35);
                    break;

                case 'error':
                    // Low minor second buzz — feels wrong without being annoying
                    self._note(ctx, 220, 'sine', 0.03, now, 0.18);
                    self._note(ctx, 233, 'triangle', 0.025, now, 0.18);
                    self._note(ctx, 196, 'sine', 0.03, now + 0.12, 0.2);
                    self._note(ctx, 208, 'triangle', 0.02, now + 0.12, 0.2);
                    break;

                case 'notification':
                    // Two-tone chime — bright and friendly (like iMessage)
                    self._note(ctx, 880, 'sine', 0.03, now, 0.15);
                    self._note(ctx, 1760, 'sine', 0.008, now, 0.1);
                    self._note(ctx, 1175, 'sine', 0.035, now + 0.12, 0.22);
                    self._note(ctx, 2349, 'sine', 0.008, now + 0.12, 0.15);
                    break;

                case 'money':
                    // Cash register ka-ching — metallic shimmer with ring
                    self._note(ctx, 1319, 'sine', 0.03, now, 0.08);
                    self._note(ctx, 2637, 'sine', 0.012, now, 0.06);
                    self._note(ctx, 1568, 'sine', 0.025, now + 0.06, 0.08);
                    self._note(ctx, 2093, 'sine', 0.04, now + 0.1, 0.4);
                    self._note(ctx, 2093, 'triangle', 0.015, now + 0.1, 0.4);
                    self._note(ctx, 4186, 'sine', 0.008, now + 0.1, 0.25);
                    self._note(ctx, 2637, 'sine', 0.02, now + 0.15, 0.35);
                    break;

                case 'alert':
                    // Two-tone warning — firm but not harsh
                    self._note(ctx, 740, 'sine', 0.04, now, 0.12);
                    self._note(ctx, 740, 'triangle', 0.02, now, 0.12);
                    self._note(ctx, 587, 'sine', 0.045, now + 0.15, 0.18);
                    self._note(ctx, 587, 'triangle', 0.02, now + 0.15, 0.18);
                    break;
            }
        },

        toggle: function() {
            this.enabled = !this.enabled;
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
    };

    var NotificationPoller = {
        lastCheck: null,
        interval: null,
        badgeEl: null,

        start: function() {
            // Only poll on authenticated pages (not portal/login)
            if (document.querySelector('[data-no-poll]') || window.location.pathname.startsWith('/p/') || window.location.pathname === '/login') {
                return;
            }

            this.lastCheck = new Date().toISOString();
            this.badgeEl = document.getElementById('notification-badge');

            // Poll every 30 seconds
            var self = this;
            this.interval = setInterval(function() { self.poll(); }, 30000);
        },

        poll: function() {
            var self = this;
            var url = '/notifications/api/poll';
            if (this.lastCheck) {
                url += '?since=' + encodeURIComponent(this.lastCheck);
            }

            fetch(url)
                .then(function(r) { return r.ok ? r.json() : null; })
                .then(function(data) {
                    if (!data) return;

                    // Update badge count
                    if (self.badgeEl) {
                        if (data.unread_count > 0) {
                            self.badgeEl.textContent = data.unread_count > 99 ? '99+' : data.unread_count;
                            self.badgeEl.style.display = '';
                        } else {
                            self.badgeEl.style.display = 'none';
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
                                    '<div class="toast-title">' + n.title + '</div>' +
                                    '<div class="toast-message">' + n.message + '</div>' +
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
                })
                .catch(function() { /* silent fail — network hiccup */ });
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
