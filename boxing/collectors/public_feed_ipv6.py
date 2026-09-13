"""Process-scoped DNS64 for public HTTPS feeds on the IPv6-only Google VM.

Original hostnames, SNI and certificate verification are preserved. Credentials
and email delivery never use this route. No system network settings are changed.
"""
import socket,subprocess,ipaddress,functools
HOSTS={'github.com','api.github.com','release-assets.githubusercontent.com',
       'objects.githubusercontent.com','sports.core.api.espn.com','site.api.espn.com'}
ORIGINAL=socket.getaddrinfo
@functools.lru_cache(maxsize=32)
def translated(host):
 for dns in ['2a00:1098:2c::1','2a01:4f8:c2c:123f::1']:
  result=subprocess.run(['dig','+short','+time=2','+tries=1','AAAA',host,'@'+dns],capture_output=True,text=True,timeout=4)
  addresses=[]
  for line in result.stdout.splitlines():
   try:addresses.append(str(ipaddress.IPv6Address(line.strip())))
   except ValueError:pass
  if addresses:return addresses[:6]
 return []
def resolve(host,port,family=0,type=0,proto=0,flags=0):
 if host in HOSTS and family in (0,socket.AF_UNSPEC,socket.AF_INET6):
  native=ORIGINAL(host,port,family,type,proto,flags)
  v6=[r for r in native if r[0]==socket.AF_INET6]
  if v6:return v6
  addresses=translated(host)
  if addresses:return [(socket.AF_INET6,type or socket.SOCK_STREAM,proto or socket.IPPROTO_TCP,'',(a,int(port),0,0)) for a in addresses]
 return ORIGINAL(host,port,family,type,proto,flags)
def enable():
 socket.getaddrinfo=resolve
