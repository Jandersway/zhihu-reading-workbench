import assert from 'node:assert/strict'
import test from 'node:test'
import { cookieFromCurlCopy } from '../app/utils/zhihuCookiePaste.ts'

const url = 'https://www.zhihu.com/api/v4/me'
const cookie = 'd_c0="device=="; z_c0=demo==; extra=t%2F^tail'

test('imports a multiline Chromium cmd copy with escaped quotes in headers and cookies', () => {
  const command = [
    'curl ^"' + url + '^"',
    '-H ^"sec-ch-ua: ^\\^"Chromium^\\^";v=^\\^"140^\\^"^"',
    '-b ^"d_c0=^\\^"device==^\\^"; z_c0=demo==; extra=t%^2F^^tail^"',
  ].join(' ^\r\n ')
  assert.deepEqual(cookieFromCurlCopy(command), { cookie })
})

test('reads an older cmd copy with plain double-quoted arguments', () => {
  const command = [
    'curl "' + url + '"',
    '-H "Cookie: d_c0=^\\^"device==^\\^"; z_c0=demo==; extra=t%^2F^^tail"',
  ].join(' ^\n ')
  assert.deepEqual(cookieFromCurlCopy(command), { cookie })
})

test('reads the newer quoting and ignores multiline request bodies', () => {
  const command = [
    'curl --url ^"' + url + '^"',
    '-b ^"d_c0=^"^"device==^"^"; z_c0=demo==; extra=t%^2F^^tail^"',
    '--data-raw ^"line one^\n\nline two^"',
  ].join(' ^\n ')
  assert.deepEqual(cookieFromCurlCopy(command), { cookie })
})

test('keeps single-line cmd import working', () => {
  assert.deepEqual(cookieFromCurlCopy('curl "' + url + '" -b "z_c0=demo=="'), { cookie: 'z_c0=demo==' })
})

test('distinguishes a missing cookie, a non-Zhihu URL, and multiple commands', () => {
  assert.match(cookieFromCurlCopy('curl "' + url + '" -H "Accept: application/json"').error, /没有携带/)
  assert.match(cookieFromCurlCopy('curl "https://www.zhihu.com.example.com" -b "z_c0=demo"').error, /www.zhihu.com/)
  assert.match(cookieFromCurlCopy('curl "' + url + '" -b "z_c0=demo" & curl "' + url + '"').error, /未识别/)
})
