(function () {
  function createSettingsCalendar({
    elements,
    state,
    constants,
    endpoints,
    callbacks,
  }) {
    const { maxOtherCalendars } = constants;
    const {
      fetchJson,
      showToast,
    } = callbacks;

    function bindCalendarControls() {
      window.APStudyFormField?.bindAutoClear?.(elements.canvasFeedUrl);
      elements.addOtherCalendar?.addEventListener('click', () => {
        const currentRows = getOtherCalendarInputValues({ includeBlank: true });
        if (currentRows.length >= maxOtherCalendars) {
          showToast(`You can add up to ${maxOtherCalendars} calendar links.`, 'error');
          return;
        }
        addOtherCalendarRow('');
        updateOtherCalendarCount();
      });
    }

    function renderOtherCalendarRows(urls) {
      if (!elements.otherCalendarLinks) {
        return;
      }
      elements.otherCalendarLinks.innerHTML = '';
      const safeUrls = Array.isArray(urls) ? urls.slice(0, maxOtherCalendars) : [];
      safeUrls.forEach((url) => addOtherCalendarRow(url));
      updateOtherCalendarCount();
    }

    function addOtherCalendarRow(value, options = {}) {
      if (!elements.otherCalendarLinks) {
        return null;
      }

      const row = document.createElement('div');
      row.className = 'settings-calendar-row';
      row.innerHTML = `
        <label class="settings-field settings-calendar-row-field">
          <span class="sr-only">Other calendar link</span>
          <span class="settings-icon-input">
            <span class="material-symbols-outlined" aria-hidden="true">event</span>
            <input data-other-calendar-url name="other_calendar_url" type="url" inputmode="url" autocomplete="off" placeholder="https://calendar.google.com/..." />
          </span>
        </label>
        <button type="button" class="settings-calendar-remove" aria-label="Remove calendar link">
          <span class="material-symbols-outlined" aria-hidden="true">close</span>
        </button>
      `;

      const input = row.querySelector('[data-other-calendar-url]');
      if (input) {
        input.value = value || '';
        input.addEventListener('input', updateOtherCalendarCount);
        window.APStudyFormField?.bindAutoClear?.(input);
      }
      row.querySelector('.settings-calendar-remove')?.addEventListener('click', () => {
        const rowIndex = Array.from(elements.otherCalendarLinks.children).indexOf(row);
        const removedUrl = input?.value.trim() || '';
        row.remove();
        updateOtherCalendarCount();
        showToast('Save to apply this change.', 'success', {
          title: 'Calendar link removed',
          duration: 10000,
          action: {
            label: 'Undo',
            onClick: () => {
              const currentRows = getOtherCalendarInputValues({ includeBlank: true });
              if (currentRows.length >= maxOtherCalendars) {
                showToast('Remove another row before restoring this link.', 'error');
                return false;
              }
              const referenceRow = elements.otherCalendarLinks.children[rowIndex] || null;
              addOtherCalendarRow(removedUrl, { before: referenceRow });
              updateOtherCalendarCount();
              showToast('Calendar link restored.', 'success');
              return false;
            },
          },
        });
      });
      if (options.before) {
        elements.otherCalendarLinks.insertBefore(row, options.before);
      } else {
        elements.otherCalendarLinks.appendChild(row);
      }
      return row;
    }

    function updateOtherCalendarCount() {
      if (!elements.otherCalendarCount) {
        return;
      }
      const rowCount = getOtherCalendarInputValues({ includeBlank: true }).length;
      elements.otherCalendarCount.textContent = `${rowCount} / ${maxOtherCalendars} added`;
    }

    function getOtherCalendarInputValues(options = {}) {
      const includeBlank = Boolean(options.includeBlank);
      if (!elements.otherCalendarLinks) {
        return [];
      }
      return Array.from(elements.otherCalendarLinks.querySelectorAll('[data-other-calendar-url]'))
        .map((input) => input.value.trim())
        .filter((value) => includeBlank || value);
    }

    function markCalendarFieldErrors() {
      const formField = window.APStudyFormField;
      if (!formField) return;
      formField.clearAll(elements.otherCalendarLinks || document);
      formField.clearInvalid(elements.canvasFeedUrl);

      const canvasUrl = elements.canvasFeedUrl?.value.trim() || '';
      const normalizedCanvasUrl = normalizeCalendarLinkForComparison(canvasUrl);
      const inputs = Array.from(elements.otherCalendarLinks?.querySelectorAll('[data-other-calendar-url]') || []);
      const seen = new Set();

      for (const input of inputs) {
        const url = input.value.trim();
        if (!url) continue;
        const normalized = normalizeCalendarLinkForComparison(url);
        if (!normalized) {
          formField.markInvalid(input);
          return;
        }
        if (normalizedCanvasUrl && normalized === normalizedCanvasUrl) {
          formField.markInvalid(input);
          return;
        }
        if (seen.has(normalized)) {
          formField.markInvalid(input);
          return;
        }
        seen.add(normalized);
      }
    }

    function collectCalendarPayload() {
      const canvasUrl = elements.canvasFeedUrl?.value.trim() || '';
      const otherUrls = getOtherCalendarInputValues();
      if (otherUrls.length > maxOtherCalendars) {
        throw new Error(`You can add up to ${maxOtherCalendars} calendar links.`);
      }

      const normalizedCanvasUrl = normalizeCalendarLinkForComparison(canvasUrl);
      const seen = new Set();
      const cleanedOtherUrls = [];

      otherUrls.forEach((url) => {
        const normalized = normalizeCalendarLinkForComparison(url);
        if (!normalized) {
          throw new Error('Each optional calendar link must be a valid http(s) or webcal URL.');
        }
        if (normalizedCanvasUrl && normalized === normalizedCanvasUrl) {
          throw new Error('Optional calendar links cannot duplicate the Canvas calendar.');
        }
        if (seen.has(normalized)) {
          throw new Error('Duplicate optional calendar links are not allowed.');
        }
        seen.add(normalized);
        cleanedOtherUrls.push(url);
      });

      return {
        canvas_ical_url: canvasUrl,
        other_ical_urls: cleanedOtherUrls,
      };
    }

    function normalizeCalendarLinkForComparison(value) {
      const raw = String(value || '').trim();
      if (!raw) {
        return '';
      }

      let parsed;
      try {
        parsed = new URL(raw);
      } catch (error) {
        return '';
      }

      let protocol = parsed.protocol.toLowerCase();
      if (protocol === 'webcal:') {
        protocol = 'https:';
      }
      if (protocol !== 'http:' && protocol !== 'https:') {
        return '';
      }

      const pathname = parsed.pathname.replace(/\/+$/, '');
      return `${protocol}//${parsed.hostname.toLowerCase()}${parsed.port ? `:${parsed.port}` : ''}${pathname}${parsed.search}`;
    }

    async function saveCalendarLinks() {
      let payload;
      try {
        payload = collectCalendarPayload();
      } catch (error) {
        markCalendarFieldErrors();
        showToast(error.message || 'Check your calendar links.', 'error');
        return;
      }

      window.APStudyFormField?.clearAll(elements.otherCalendarLinks || document);
      window.APStudyFormField?.clearInvalid(elements.canvasFeedUrl);

      const previousLabel = elements.saveCalendarLinks?.textContent || 'Save';
      if (elements.saveCalendarLinks) {
        elements.saveCalendarLinks.disabled = true;
        elements.saveCalendarLinks.textContent = 'Saving...';
      }

      try {
        const response = await fetchJson(endpoints.feedUrl, {
          method: 'POST',
          body: JSON.stringify(payload),
        });

        const savedCanvasUrl = response.canvas_ical_url ?? payload.canvas_ical_url;
        const savedOtherUrls = Array.isArray(response.other_ical_urls)
          ? response.other_ical_urls
          : payload.other_ical_urls;
        state.settings = {
          ...(state.settings || {}),
          canvas_ical_url: savedCanvasUrl,
          other_calendar_urls: savedOtherUrls,
        };
        state.otherCalendarUrls = savedOtherUrls;
        if (elements.canvasFeedUrl) {
          elements.canvasFeedUrl.value = savedCanvasUrl || '';
        }
        renderOtherCalendarRows(savedOtherUrls);
        showToast('Calendar links saved.', 'success');
      } catch (error) {
        showToast(error.message || 'Check the links and try again.', 'error', { title: 'Couldn’t save calendar links' });
      } finally {
        if (elements.saveCalendarLinks) {
          elements.saveCalendarLinks.disabled = false;
          elements.saveCalendarLinks.textContent = previousLabel;
        }
      }
    }

    return {
      bindCalendarControls,
      renderOtherCalendarRows,
      saveCalendarLinks,
    };
  }

  window.APStudySettingsCalendar = { createSettingsCalendar };
})();
