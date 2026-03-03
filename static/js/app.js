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

        // Enforce max visible toasts (remove oldest active ones first)
        while (activeToastCount() >= 5) {
            var oldest = container.querySelector('.toast:not(.toast-exit)');
            if (oldest) dismissToast(oldest);
            else break;
        }

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
        if (!toast || !toast.parentNode) return;         // Already removed
        if (toast.classList.contains('toast-exit')) return;
        toast.classList.add('toast-exit');
        setTimeout(function() {
            if (toast.parentNode) toast.remove();
        }, 300);
    }

    /** Count only visible (non-exiting) toasts */
    function activeToastCount() {
        var container = document.getElementById('toast-container');
        if (!container) return 0;
        return container.querySelectorAll('.toast:not(.toast-exit)').length;
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


    /* ── Clean Sound Engine v4 ── */
    /* Simple, reliable notification sounds using Web Audio API with minimal processing */
    var audioCtx = null;
    function getAudioCtx() {
        if (!audioCtx) {
            try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
            catch(e) { return null; }
        }
        if (audioCtx.state === 'suspended') audioCtx.resume();
        return audioCtx;
    }

    var Sound = {
        enabled: localStorage.getItem('concreteiq_sounds') !== 'false',
        _volume: 0.3,

        /* Simple clean tone with smooth envelope */
        _tone: function(ctx, freq, vol, start, dur, dest) {
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = freq;
            osc.connect(gain);
            gain.connect(dest);
            gain.gain.setValueAtTime(0, start);
            gain.gain.linearRampToValueAtTime(vol, start + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.001, start + dur);
            osc.start(start);
            osc.stop(start + dur + 0.05);
        },

        play: function(type) {
            if (!this.enabled) return;
            var ctx = getAudioCtx();
            if (!ctx) return;

            var now = ctx.currentTime;
            var v = this._volume;
            var dest = ctx.destination;

            switch(type) {
                case 'click':
                    this._tone(ctx, 800, v * 0.3, now, 0.06, dest);
                    break;

                case 'success':
                    /* Clean ascending two-tone chime: C5 → G5 */
                    this._tone(ctx, 523, v * 0.5, now, 0.25, dest);
                    this._tone(ctx, 784, v * 0.6, now + 0.12, 0.35, dest);
                    break;

                case 'error':
                    /* Soft descending tone: E4 → C4 */
                    this._tone(ctx, 330, v * 0.4, now, 0.2, dest);
                    this._tone(ctx, 262, v * 0.5, now + 0.15, 0.3, dest);
                    break;

                case 'notification':
                    /* Clean two-note chime: G5 → D6 */
                    this._tone(ctx, 784, v * 0.4, now, 0.3, dest);
                    this._tone(ctx, 1175, v * 0.45, now + 0.15, 0.4, dest);
                    break;

                case 'money':
                    /* Bright ascending triad: C5 → E5 → G5 */
                    this._tone(ctx, 523, v * 0.4, now, 0.2, dest);
                    this._tone(ctx, 659, v * 0.45, now + 0.1, 0.2, dest);
                    this._tone(ctx, 784, v * 0.5, now + 0.2, 0.4, dest);
                    break;

                case 'alert':
                    /* Double pulse: A5 → F5, repeat */
                    this._tone(ctx, 880, v * 0.5, now, 0.12, dest);
                    this._tone(ctx, 698, v * 0.55, now + 0.1, 0.15, dest);
                    this._tone(ctx, 880, v * 0.35, now + 0.35, 0.1, dest);
                    this._tone(ctx, 698, v * 0.4, now + 0.45, 0.15, dest);
                    break;
            }
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
        _seenIds: {},               // Track notification IDs to prevent duplicates
        _lastSoundTime: 0,          // Throttle sounds (max 1 per 5 seconds)
        _firstPoll: true,           // Suppress toasts on first poll (page load)
        _failCount: 0,              // Track consecutive failures for backoff
        MAX_TOASTS: 4,              // Max visible toasts at once
        MAX_BATCH_TOASTS: 3,        // Max toasts per poll batch (don't flood)
        POLL_INTERVAL: 10000,       // 10 seconds foreground
        POLL_INTERVAL_BG: 60000,    // 60 seconds when tab hidden
        SOUND_COOLDOWN: 5000,       // Min 5s between notification sounds
        TOAST_DURATION: 8000,       // Auto-dismiss after 8s

        start: function() {
            // Only poll on authenticated pages (not portal/login)
            if (document.querySelector('[data-no-poll]') ||
                window.location.pathname.startsWith('/p/') ||
                window.location.pathname === '/login') {
                return;
            }

            this.lastCheck = new Date().toISOString();
            this.badgeEl = document.getElementById('notification-badge');
            this._firstPoll = true;

            var self = this;

            // First poll after 2s (let page fully load)
            setTimeout(function() { self.poll(); }, 2000);

            // Start regular polling
            this._startInterval(this.POLL_INTERVAL);

            // Handle tab visibility changes
            document.addEventListener('visibilitychange', function() {
                if (document.visibilityState === 'visible') {
                    // Tab came back — poll and switch to fast interval
                    self.poll();
                    self._startInterval(self.POLL_INTERVAL);
                } else {
                    // Tab hidden — slow down to save resources
                    self._startInterval(self.POLL_INTERVAL_BG);
                }
            });
        },

        _startInterval: function(ms) {
            var self = this;
            if (this.interval) clearInterval(this.interval);
            this.interval = setInterval(function() { self.poll(); }, ms);
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
                .then(function(r) {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json();
                })
                .then(function(data) {
                    if (!data) return;
                    self._failCount = 0;  // Reset on success

                    // ── Update badge ──
                    self._updateBadge(data.unread_count || 0);

                    // ── Show toasts for new notifications ──
                    if (data.notifications && data.notifications.length > 0) {
                        self._processNotifications(data.notifications);
                    }

                    // ── Sync Alpine dropdown ──
                    if (data.recent) {
                        self._syncDropdown(data.recent);
                    }

                    // After first poll, allow toasts for subsequent polls
                    self._firstPoll = false;
                })
                .catch(function(err) {
                    self._failCount++;
                    // Back off on repeated failures (max 60s)
                    if (self._failCount > 3) {
                        var backoff = Math.min(self._failCount * 10000, 60000);
                        self._startInterval(backoff);
                    }
                })
                .finally(function() { self._polling = false; });
        },

        _updateBadge: function(count) {
            if (!this.badgeEl) return;
            if (count > 0) {
                this.badgeEl.textContent = count > 99 ? '99+' : count;
                this.badgeEl.style.display = '';
                this.badgeEl.style.background = '#ef4444';
                this.badgeEl.style.color = '#fff';
            } else {
                this.badgeEl.textContent = '0';
                this.badgeEl.style.background = 'rgba(255,255,255,0.15)';
                this.badgeEl.style.color = 'rgba(255,255,255,0.35)';
                this.badgeEl.style.display = '';
            }
        },

        _processNotifications: function(notifications) {
            var self = this;
            var container = getToastContainer();
            var now = Date.now();
            var toastsShown = 0;
            var playedSound = false;

            // Filter to only unseen notifications
            var unseen = [];
            notifications.forEach(function(n) {
                // Robust dedup: prefer numeric id, fall back to string hash
                var nid = (n.id != null) ? String(n.id) : (n.title + '|' + n.created_at);
                if (!self._seenIds[nid]) {
                    self._seenIds[nid] = now;
                    n._nid = nid;
                    unseen.push(n);
                }
            });

            if (unseen.length === 0) {
                // Still update lastCheck even if all were dupes
                this._updateLastCheck(notifications);
                return;
            }

            // On first poll (page load), just mark as seen — don't show toasts
            // This prevents the flood of old notifications when you open the page
            if (this._firstPoll) {
                this._updateLastCheck(notifications);
                return;
            }

            // Stagger toasts with a slight delay between each (200ms)
            unseen.forEach(function(n, idx) {
                // Cap how many toasts we show per batch
                if (toastsShown >= self.MAX_BATCH_TOASTS) return;

                setTimeout(function() {
                    // Re-check limit before adding (another toast might have been added)
                    while (activeToastCount() >= self.MAX_TOASTS) {
                        var oldest = container.querySelector('.toast:not(.toast-exit)');
                        if (oldest) dismissToast(oldest);
                        else break;
                    }

                    var toastType = NOTIF_TOAST_MAP[n.type] || 'info';
                    var soundType = NOTIF_SOUND_MAP[n.type] || 'notification';

                    // Create toast element
                    var toast = document.createElement('div');
                    toast.className = 'toast toast-' + toastType;
                    toast.setAttribute('data-nid', n._nid);
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
                        '<div class="toast-progress"><div class="toast-progress-bar" style="animation-duration: ' + self.TOAST_DURATION + 'ms"></div></div>';

                    toast.querySelector('.toast-close').addEventListener('click', function() {
                        dismissToast(toast);
                    });

                    container.appendChild(toast);

                    // Play sound (throttled — max 1 per cooldown period)
                    var soundNow = Date.now();
                    if (!playedSound && (soundNow - self._lastSoundTime) > self.SOUND_COOLDOWN) {
                        Sound.play(soundType);
                        self._lastSoundTime = soundNow;
                        playedSound = true;
                    }

                    // Auto-dismiss
                    setTimeout(function() { dismissToast(toast); }, self.TOAST_DURATION);

                }, idx * 250);  // 250ms stagger between toasts

                toastsShown++;
            });

            // If there are more unseen notifications than we showed, add a summary
            if (unseen.length > self.MAX_BATCH_TOASTS) {
                var remaining = unseen.length - self.MAX_BATCH_TOASTS;
                setTimeout(function() {
                    while (activeToastCount() >= self.MAX_TOASTS) {
                        var oldest = container.querySelector('.toast:not(.toast-exit)');
                        if (oldest) dismissToast(oldest);
                        else break;
                    }
                    var summary = document.createElement('div');
                    summary.className = 'toast toast-info';
                    summary.innerHTML =
                        '<div class="toast-accent"></div>' +
                        TOAST_ICONS.info +
                        '<div class="toast-body">' +
                            '<div class="toast-title">More Notifications</div>' +
                            '<div class="toast-message">+ ' + remaining + ' more notification' + (remaining > 1 ? 's' : '') + '</div>' +
                        '</div>' +
                        '<button class="toast-close" aria-label="Close">' +
                            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>' +
                        '</button>' +
                        '<div class="toast-progress"><div class="toast-progress-bar" style="animation-duration: ' + self.TOAST_DURATION + 'ms"></div></div>';
                    summary.querySelector('.toast-close').addEventListener('click', function() {
                        dismissToast(summary);
                    });
                    container.appendChild(summary);
                    setTimeout(function() { dismissToast(summary); }, self.TOAST_DURATION);
                }, self.MAX_BATCH_TOASTS * 250 + 100);
            }

            this._updateLastCheck(notifications);
            this._cleanSeenIds();
        },

        _updateLastCheck: function(notifications) {
            var last = notifications[notifications.length - 1];
            if (last && last.created_at) {
                this.lastCheck = last.created_at;
            }
        },

        _cleanSeenIds: function() {
            // Remove seen IDs older than 5 minutes (keep memory bounded)
            var cutoff = Date.now() - 300000;
            var keys = Object.keys(this._seenIds);
            if (keys.length > 100) {
                for (var i = 0; i < keys.length; i++) {
                    if (this._seenIds[keys[i]] < cutoff) {
                        delete this._seenIds[keys[i]];
                    }
                }
            }
        },

        _syncDropdown: function(recent) {
            // Try Alpine v3 first, then v2
            var dropdownEl = document.querySelector('[x-data*="notifOpen"]');
            if (!dropdownEl) return;

            if (dropdownEl._x_dataStack) {
                // Alpine v3
                dropdownEl._x_dataStack[0].items = recent;
            } else if (dropdownEl.__x) {
                // Alpine v2
                dropdownEl.__x.$data.items = recent;
            }
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
