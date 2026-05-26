import { describe, expect, it } from 'vitest';
import { isAllowedExternalMediaUrl, isAllowedExternalUrl } from '../../../src/utils/urlSafety';

describe('isAllowedExternalUrl', () => {
  it('allows https URLs', () => {
    expect(isAllowedExternalUrl('https://example.com')).toBe(true);
  });

  it('allows http URLs', () => {
    expect(isAllowedExternalUrl('http://example.com')).toBe(true);
  });

  it('allows mailto URLs', () => {
    expect(isAllowedExternalUrl('mailto:user@example.com')).toBe(true);
  });

  it('rejects javascript: URLs', () => {
    expect(isAllowedExternalUrl('javascript:alert(1)')).toBe(false);
  });

  it('rejects data: URLs', () => {
    expect(isAllowedExternalUrl('data:text/html,<h1>hi</h1>')).toBe(false);
  });

  it('rejects ftp: URLs', () => {
    expect(isAllowedExternalUrl('ftp://example.com')).toBe(false);
  });

  it('rejects invalid URLs', () => {
    expect(isAllowedExternalUrl('not a url')).toBe(false);
  });

  it('rejects empty string', () => {
    expect(isAllowedExternalUrl('')).toBe(false);
  });

  it('rejects file: URLs', () => {
    expect(isAllowedExternalUrl('file:///etc/passwd')).toBe(false);
  });
});

describe('isAllowedExternalMediaUrl', () => {
  it('allows http and https media URLs case-insensitively', () => {
    expect(isAllowedExternalMediaUrl('http://example.com/video.mp4')).toBe(true);
    expect(isAllowedExternalMediaUrl('HTTPS://example.com/video.mp4')).toBe(true);
  });

  it('rejects non-media external protocols', () => {
    expect(isAllowedExternalMediaUrl('mailto:user@example.com')).toBe(false);
    expect(isAllowedExternalMediaUrl('javascript:alert(1)')).toBe(false);
    expect(isAllowedExternalMediaUrl('data:video/mp4;base64,AAAA')).toBe(false);
  });

  it('rejects relative and invalid URLs', () => {
    expect(isAllowedExternalMediaUrl('../media/video.mp4')).toBe(false);
    expect(isAllowedExternalMediaUrl('not a url')).toBe(false);
  });
});
