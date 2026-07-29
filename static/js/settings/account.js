(function registerSettingsAccount(global) {
  function createSettingsAccount({
    elements,
    state,
    endpoints,
    callbacks,
  }) {
    const {
      downloadJson,
      fetchJson,
      showToast,
    } = callbacks;

    async function handlePasswordReset() {
      const email = (state.profile && state.profile.email)
        || (state.account && state.account.email)
        || elements.email?.value
        || '';
      if (!email) {
        global.APStudyFormField?.markInvalid?.(elements.email);
        showToast('Email address is required for password recovery.', 'error');
        return;
      }
      global.APStudyFormField?.clearInvalid?.(elements.email);

      try {
        await fetchJson(endpoints.passwordRecovery, { method: 'POST' });
        showToast('Password reset email sent.', 'success');
      } catch (error) {
        console.error(error);
        showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t send reset email' });
      }
    }

    async function handleDeleteAccount() {
      const confirmed = await (global.APStudyConfirm?.request?.({
        title: 'Delete account?',
        message: 'This removes your profile, settings, and saved data.',
        acceptLabel: 'Delete account',
        danger: true,
      }) ?? Promise.resolve(false));
      if (!confirmed) {
        return;
      }

      if (global.APStudyUndo?.stage) {
        global.APStudyUndo.stage({
          title: 'Account deletion scheduled',
          message: 'Your account will be deleted when this notice closes.',
          type: 'warning',
          commit: ({ reason }) => fetchJson(endpoints.deleteAccount, {
            method: 'POST',
            keepalive: reason === 'pagehide',
          }),
          restore: () => {},
          onUndo: () => showToast('Your account was kept.', 'success'),
          onCommit: () => global.location.assign('/logout'),
          errorTitle: 'Couldn’t delete account',
        });
        return;
      }

      try {
        await fetchJson(endpoints.deleteAccount, { method: 'POST' });
        global.location.assign('/logout');
      } catch (error) {
        showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t delete account' });
      }
    }

    async function handleExportData() {
      try {
        const data = await fetchJson(endpoints.exportData);
        downloadJson(`apstudy-export-${Date.now()}.json`, data);
        showToast('Export started.', 'success');
      } catch (error) {
        showToast(error.message || 'Try again in a moment.', 'error', { title: 'Couldn’t export your data' });
      }
    }

    return {
      handleDeleteAccount,
      handleExportData,
      handlePasswordReset,
    };
  }

  global.APStudySettingsAccount = {
    createSettingsAccount,
  };
})(window);
