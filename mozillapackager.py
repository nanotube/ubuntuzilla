#!/usr/bin/env python3
##
##############################################################################
##
## Ubuntuzilla: package official Mozilla builds of Mozilla software on Ubuntu Linux
## Copyright (C) 2009  Daniel Folkinshteyn <nanotube@users.sf.net>
##
## http://ubuntuzilla.sourceforge.net/
##
## This program is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License
## as published by the Free Software Foundation; either version 3
## of the License, or (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <http://www.gnu.org/licenses/>.
##
##############################################################################

##############################################################################
##
## Some notes about the general structure of this program.
##
## The idea is such that any action that is common to at least two of Firefox,
## Thunderbird, or Seamonkey is coded in the base class called 
## 'MozillaInstaller'. 
##
## Classes 'SeamonkeyInstaller', 'FirefoxInstaller', and 
## 'ThunderbirdInstaller' derive from the base 'MozillaInstaller' class, and 
## include all the package-specific actions. 
##
## This may seem a bit too complex, but it really simplifies code maintenance
## and the addition of new features, as it reduces the necessity of changing 
## the same code several times. 
##
## The 'BaseStarter' class processes the command line options, and decides
## what to do accordingly.
##
## The 'VersionInfo' class is just a simple repository of version and other
## descriptive information about this software.
##
## The 'UtilityFunctions' class has some general functions that don't belong
## in the Mozilla classes.
##
##############################################################################

from optparse import OptionParser
import optparse
import re
import os, os.path
import sys
import stat
import time
import shutil
import subprocess
import dbus
import urllib.request, urllib.error, urllib.parse
import traceback
import signal # used to workaround the python sigpipe bug

# todo: internationalization: figure out how to use the whole i18n thing, break out the text messages into separate files, and hopefully get some translators to work on those.

# some terminal escape sequences to make bold text
bold = "\033[1m"
unbold = "\033[0;0m"

class VersionInfo:
    '''Version information storage
    '''
    def __init__(self):
        self.name = "ubuntuzilla"
        self.version = "0.0.1"
        self.description = "Packager of Mozilla Builds of Mozilla Software for Ubuntu"
        self.url = "http://ubuntuzilla.sourceforge.net/"
        self.license = "GPL"
        self.author = "Daniel Folkinshteyn"
        self.author_email = "nanotube@users.sourceforge.net"
        self.platform = "Ubuntu Linux"

# Let's define some exceptions
class UbuntuzillaError(Exception): pass
class InsufficientDiskSpaceError(UbuntuzillaError): pass
class SystemCommandExecutionError(UbuntuzillaError): pass

class UtilityFunctions:
    '''This class is for holding some functions that are of general use, and thus
    do not belong in the mozilla class and its derivatives.
    '''
    
    def __init__(self, options):
        self.options=options
        self.version = VersionInfo()
    
    def getSystemOutput(self, executionstring, numlines=1, errormessage="Previous command has failed to complete successfully. Exiting."):
        '''Read output from an external command, exit if command fails.
        This is a simple wrapper for subprocess.Popen()
        For numlines==0, return whole list, otherwise, return requested number of lines.
        Result is a list, one line per item. 
        If numlines is 1, then result is a string.'''
        
        p = subprocess.Popen(executionstring, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, close_fds=True, encoding="UTF-8")
        returncode = p.wait()
        result = p.stdout.readlines()
        
        # need this separate check for w3m, since its return code is 0 even if it fails to find the site.
        if re.search("w3m", executionstring):
            if len(result) == 0 or re.match("w3m: Can't load", result[0]):
                errormessage = '\n'.join(result) + errormessage
                returncode = 1
        
        if returncode != 0:
            print(executionstring, file=sys.stderr)
            print(errormessage, file=sys.stderr)
            print("Process returned code", returncode, file=sys.stderr)
            print(result, file=sys.stderr)
            raise SystemCommandExecutionError("Command has not completed successfully. If this problem persists, please seek help at our website, " + self.version.url)
        
        else:
            for i in range(0,len(result)):
                result[i] = result[i].strip()
            if numlines == 1:
                return result[0]
            elif numlines == 0:
                return result
            else:
                return result[0:numlines]
    
    def subprocess_setup(self):
        # Python installs a SIGPIPE handler by default. This is usually not what
        # non-Python subprocesses expect.
        # see http://bugs.python.org/issue1652
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    def execSystemCommand(self, executionstring, includewithtest=False, errormessage="Previous command has failed to complete successfully. Exiting."):
        '''Execute external command. Throw exception if command exits with non-zero status.
        This is a simple wrapper for subprocess.call()'''
        
        if (not self.options.test) or includewithtest:
            returncode = subprocess.call(executionstring, preexec_fn=self.subprocess_setup, shell=True)
            if returncode:
                print(executionstring, file=sys.stderr)
                print(errormessage, file=sys.stderr)
                print("Process returned code", returncode, file=sys.stderr)
                raise SystemCommandExecutionError("Command has not completed successfully. If this problem persists, please seek help at our website, " + self.version.url)
    
    def robustDownload(self, argsdict, errormsg="Download failed. This may be due to transient network problems, so try again later. Exiting.", repeat=5, onexit = sys.exit):
        '''try the download several times, in case we get a bad mirror (happens 
        with a certain regularity), or some other transient network problem)
        
        note: repeat argument is not used anymore, we now iterate over mirror list'''
        
        #for i in xrange(repeat):
        origexecstring = argsdict['executionstring']
        for mirror in self.options.mirrors:
            try:
                argsdict['executionstring'] = re.sub("%mirror%",mirror,origexecstring)
                self.execSystemCommand(**argsdict)
                break
            except SystemCommandExecutionError:
                print("Error downloading. Trying again, hoping for a different mirror.")
                time.sleep(2)
        else:
            print(errormsg)
            onexit(1)


class BaseStarter:
    '''Parses options, and initiates the right actions.
    '''
    def __init__(self):
        self.version = VersionInfo()
        self.ParseOptions()
        
    def ParseOptions(self):
        '''Read command line options
        '''
        def prepend_callback(option, opt_str, value, parser):
            default_values = getattr(parser.values, option.dest)
            default_values.insert(0, value)
            setattr(parser.values, option.dest, default_values)
        
        parser = OptionParser(
                        version=self.version.name.capitalize() + " version " +self.version.version + "\nProject homepage: " + self.version.url, 
                        description="The Ubuntuzilla script can install the official Mozilla build of Firefox, Thunderbird, or Seamonkey, on an Ubuntu Linux system, in parallel with any existing versions from the repositories. For a more detailed usage manual, see the project homepage: " + self.version.url, 
                        formatter=optparse.TitledHelpFormatter(),
                        usage="%prog [options]\n or \n  python %prog [options]")
        parser.add_option("-d", "--debug", action="store_true", dest="debug", help="debug mode (print some extra debug output). [default: %default]")
        parser.add_option("-t", "--test", action="store_true", dest="test", help="make a dry run, without actually installing anything. [default: %default]")
        parser.add_option("-p", "--package", type="choice", action="store", dest="package", choices=['firefox','firefox-esr','thunderbird','seamonkey'], help="which package to work on: firefox, firefoxesr, thunderbird, or seamonkey. [default: %default]")
        parser.add_option("-a", "--action", type="choice", action="store", dest="action", choices=['getversion','builddeb','adddebtorepo','uploadrepo','cleanup','all',], help="what to do with the selected package: builddeb creates the .deb; adddebtorepo updates the repository; uploadrepo uploads the repository; cleanup removes temporary files; all does all of this from start to finish. [default: %default]")
        parser.add_option("-g", "--skipgpg", action="store_true", dest="skipgpg", help="skip gpg signature verification. [default: %default]")
        parser.add_option("-u", "--unattended", action="store_true", dest="unattended", help="run in unattended mode. [default: %default]")
        #parser.add_option("-l", "--localization", action="store", dest="localization", help="for use with unattended mode only. choose localization (language) for your package of choice. note that the burden is on you to make sure that this localization of your package actually exists. [default: %default]")
        parser.add_option("-v", "--debversion", action="store", dest="debversion", help="The ubuntu-version of the package to create. To be used in case a bad package was pushed out, and users should be upgraded to a fresh repack. [default: %default]")
        parser.add_option("-b", "--debdir", action="store", dest="debdir", help="Directory where to stick the completed .deb file. [default: %default]")
        parser.add_option("-r", "--targetdir", action="store", dest="targetdir", help="installation/uninstallation target directory for the .deb. [default: %default]")
        parser.add_option("-i", "--arch", type="choice", action="store", dest="arch", choices=['i686','x86_64'], help="choose architecture: i686 or x86_64. [default: %default]")
        parser.add_option("-m", "--mirror", action="callback", callback=prepend_callback, type="string", dest="mirrors", help="Prepend a mozilla mirror server to the default list of mirrors. Use ftp mirrors only. Include path component up to the 'firefox', 'thunderbird', or 'seamonkey' directories. (See http://www.mozilla.org/mirrors.html for list of mirrors). [default: %default]")
        parser.add_option("-k", "--keyservers", action="callback", callback=prepend_callback, type="string", dest="keyservers", help="Prepend a pgp keyserver to the default list of keyservers. [default: %default]")
        
        parser.set_defaults(debug=False, 
                test=False, 
                package="firefox",
                action="all",
                skipgpg=False,
                unattended=False,
                debversion='1',
                #localization="en-US",
                #skipbackup=False,
                debdir=os.getcwd(),
                targetdir="/opt",
                arch="i686",
                mirrors=['http://releases.mozilla.org/pub/','https://archive.seamonkey-project.org'],
                keyservers = ['subkeys.pgp.net',
                        'pgpkeys.mit.edu',
                        'pgp.mit.edu',
                        'wwwkeys.pgp.net',
                        'keymaster.veridis.com'])
        
        (self.options, args) = parser.parse_args()
        if self.options.debug:
            print("Your commandline options:\n", self.options)
        
    def start(self):
        #if self.options.action != 'updateubuntuzilla':
        self.check_uid()
        if self.options.package == 'firefox':
            fi = FirefoxInstaller(self.options)
            fi.start()
        elif self.options.package == 'firefox-esr':
            fi = FirefoxESRInstaller(self.options)
            fi.start()
        elif self.options.package == 'thunderbird':
            ti = ThunderbirdInstaller(self.options)
            ti.start()
        elif self.options.package == 'seamonkey':
            si = SeamonkeyInstaller(self.options)
            si.start()
        #else:
            #ub = UbuntuzillaUpdater(self.options)
            #ub.start()
    
    def check_uid(self):
        if os.getuid() == 0:
            print("\nYou appear to be trying to run Ubuntuzilla as root.\nUbuntuzilla really shouldn't be run as root under normal circumstances.\nYou are advised to exit now and run it as regular user, without 'sudo'.\nDo not continue, unless you know what you're doing.\nDo you want to exit now?")
            while 1:
                ans = input("Please enter 'y' or 'n': ")
                if ans in ['y','Y','n','N']:
                    ans = ans.lower()
                    break
            
            if ans == 'y':
                print("Please run Ubuntuzilla again, without sudo.")
                sys.exit()
            else:
                print("Hope you know what you're doing... Continuing...")
                    
    
class MozillaInstaller:
    '''Generic installer class, from which Firefox, Seamonkey, and Thunderbird installers will be derived.
    '''
    def __init__(self, options):
        self.options = options
        self.version = VersionInfo()
        self.util = UtilityFunctions(options)
        self.keySuccess = False
        if self.options.test:
            print("Testing mode ON.")
        if self.options.debug:
            print("Debug mode ON.")
        os.chdir('/tmp')
        self.debdir = os.path.join('/tmp',self.options.package + 'debbuild', 'debian')
        self.packagename = self.options.package + '-mozilla-build'
        self.debarch = {'i686':'i386','x86_64':'amd64'}

    def start(self):
        if self.options.action in ['builddeb','adddebtorepo','uploadrpo','cleanup','all']:
            self.welcome()
        
        self.getLatestVersion()
        if self.options.action in ['getversion']:
            print(self.releaseVersion)
        
        if self.options.action in ['builddeb','adddebtorepo','uploadrpo','cleanup','all']:
            self.confirmLatestVersion()
        
        if self.options.action in ['builddeb','all']:
            self.downloadPackage()
            if not self.options.skipgpg:
                self.downloadGPGSignature()
                self.getMozillaGPGKey()
                #self.verifyGPGSignature()
            self.getMD5Sum()
            self.verifyMD5Sum()
            self.createDebStructure()
            self.extractArchive()
            self.createSymlinks()
            self.createMenuItem()
            self.createDeb()
        if self.options.action in ['adddebtorepo','all']:
            self.createRepository()
        if self.options.action in ['uploadrepo','all']:
            self.syncRepository()
        if self.options.action in ['cleanup','all']:
            self.cleanup()
        
        if self.options.action in ['builddeb','adddebtorepo','uploadrpo','cleanup','all']:
            self.printSuccessMessage()

    def welcome(self):
        print("\nWelcome to Ubuntuzilla Packager version " + self.version.version + "\n\nUbuntuzilla Packager creates a .deb file out of the latest release of Firefox, Thunderbird, or Seamonkey.\n\nThis script will now build the .deb of latest release of the official Mozilla build of " + self.options.package.capitalize() + ". If you run into any problems using this script, or have feature requests, suggestions, or general comments, please visit our website at", self.version.url, "\n")
        
        print("\nThe action you have requested is: " + bold + self.options.action + unbold + '\n')

    def getLatestVersion(self): # done in child, in self.releaseVersion
        if self.options.action != 'getversion': # this should only output the version and be quiet otherwise
            print("Retrieving the version of the latest release of " + self.options.package.capitalize() + " from the Mozilla website...")
        # child-specific implementation comes in here

    def confirmLatestVersion(self):
        print(bold + "The most recent release of " + self.options.package.capitalize() + " is detected to be " + self.releaseVersion + "." + unbold)
        print("\nPlease make sure this is correct before proceeding. (You can confirm by going to http://www.mozilla.org/)")
        print("If no version number shows, if the version shown is not the latest, or if you would like to use a different release, press 'n', and you'll be given the option to enter the version manually. Otherwise, press 'y', and proceed with installation. [y/n]? ")
        self.askyesno()
        if self.ans == 'y':
            pass
        else:
            print("\nIf no version shows, or it does not agree with the latest version as listed on http://www.mozilla.org, please visit our website at", self.version.url, "and let us know.")
            print("If you would like to enter the version manually and proceed with installation, you can do so now. Note that beta and release candidate versions are now allowed, but you use pre-release software at your own risk!\n")
            
            while 1:
                self.ans = input("Please enter the version of "+ self.options.package.capitalize() + " you wish to install, or 'q' to quit: ")
                if self.ans == 'q':
                    print('Quitting by user request...')
                    sys.exit()
                else:
                    self.releaseVersion = self.ans
                    print("You have chosen version '" + self.releaseVersion + "'. Is that correct [y/n]?")
                    self.askyesno()
                    if self.ans == 'y':
                        break
    
    def downloadPackage(self, sm=False): 
        # we are going to dynamically determine the package name
        print("Retrieving package name for", self.options.package.capitalize(), "...")
        if not sm:
            pkg = self.options.package
            for mirror in self.options.mirrors:
                try:
                    self.packageFilename = self.util.getSystemOutput(executionstring="curl --no-progress-meter " + mirror + pkg + "/releases/" + self.releaseVersion + "/linux-" + self.options.arch + "/en-US/ | w3m -dump -T text/html | grep '" + self.options.package + ".*tar\.bz2\|xz' | awk '{print $2}'", numlines=1)
                    print("Success!: " + self.packageFilename)
                    break
                except SystemCommandExecutionError:
                    print("Download error. Trying again, hoping for a different mirror.")
                    time.sleep(2)
            else:
                print("Failed to retrieve package name. This may be due to transient network problems, so try again later. If the problem persists, please seek help on our website,", self.version.url)
                sys.exit(1)
        else:
            print("Doing a different thing for seamonkey package filename.")

        
    def downloadGPGSignature(self): # done, self.sigFilename
        pass
        #self.sigFilename = self.packageFilename + ".asc"
        #print "\nDownloading " + self.options.package.capitalize() + " signature from the Mozilla site\n"
        
        #self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 ftp://" + "%mirror%" + self.options.package + "/releases/" + self.releaseVersion + "/linux-" + self.options.arch + "/en-US/" + self.sigFilename, 'includewithtest':True}, errormsg="Failed to retrieve signature file. This may be due to transient network problems, so try again later. Exiting.")
        
    def getMozillaGPGKey(self):
        ''' If key doesn't already exist on the system, retrieve key from keyserver.
        Try each keyserver in the list several times, sleep 2 secs between retries.'''
        
        # 812347DD - old mozilla software releases key
        # 0E3606D9 - current mozilla software releases key
        # 6CE2996F - mozilla messaging (thunderbird) key
        
        try:
            self.util.execSystemCommand("gpg --list-keys --with-colons 0E3606D9", includewithtest=True, errormessage="Mozilla GPG key not present on the system. Will attempt to retrieve from keyserver.")
            self.util.execSystemCommand("gpg --list-keys --with-colons 812347DD", includewithtest=True, errormessage="Mozilla GPG key not present on the system. Will attempt to retrieve from keyserver.")
            self.util.execSystemCommand("gpg --list-keys --with-colons 6CE2996F", includewithtest=True, errormessage="Mozilla GPG key not present on the system. Will attempt to retrieve from keyserver.")
        except SystemCommandExecutionError:
            print("\nImporting Mozilla Software Releases public key\n")
            print("Note that if you have never used gpg before on this system, and this is your first time running this script, there may be a delay of about a minute during the generation of a gpg keypair. This is normal and expected behavior.\n")
            
            for i in range(0,5):
                for keyserver in self.options.keyservers:
                    try:
                        self.util.execSystemCommand("gpg --keyserver " + keyserver + " --recv 0E3606D9 812347DD 6CE2996F", includewithtest=True)
                        self.keySuccess = True
                        print("Successfully retrieved Mozilla Software Releases Public key from", keyserver, ".\n")
                        break
                    except:
                        print("Unable to retrieve Mozilla Software Releases Public key from", keyserver, ". Trying again...")
                        time.sleep(2)
                if self.keySuccess:
                    break
            if not self.keySuccess:
                print("Failed to retrieve Mozilla Software Releases Public key from any of the listed keyservers. Please check your network connection, and try again later.\n")
                sys.exit(1)

    def verifyGPGSignature(self):
        print("\nVerifying signature...\nNote: do not worry about \"untrusted key\" warnings. That is normal behavior for newly imported keys.\n")
        #returncode = os.system("gpg --verify " + self.sigFilename + " " + self.packageFilename)
        returncode = os.system("gpg --verify SHA512SUMS.asc SHA512SUMS")
        if returncode:
            print("GPG signature verification failed. This is most likely due to a corrupt download. You should delete files 'SHA512SUMS', 'SHA512SUMS.asc', '", self.packageFilename, "', and run the script again.\n")
            print("Would you like to delete those files now? [y/n]? ")
            self.askyesno()
            if self.ans == 'y':
                print("\nOK, deleting files and exiting.\n")
                os.remove(self.packageFilename)
                os.remove('SHA512SUMS')
                os.remove('SHA512SUMS.asc')
            else:
                print("OK, exiting without deleting files.\n")
            sys.exit(1)

    def getMD5Sum(self): # ok this is not necessarily md5...
        print("\nDownloading " + self.options.package.capitalize() + " checksums from the Mozilla site\n")
        self.sigFilename = self.packageFilename + ".sha512"
        if self.options.package == 'firefox-esr':
            package = 'firefox'
        else:
            package = self.options.package

        
        self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 -q -nv " + "%mirror%" + package + "/releases/" + self.releaseVersion + "/SHA512SUMS", 'includewithtest':True}, errormsg="Failed to retrieve checksums. This may be due to transient network problems, so try again later. Exiting.")
        self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 -q -nv " + "%mirror%" + package + "/releases/" + self.releaseVersion + "/SHA512SUMS.asc", 'includewithtest':True}, errormsg="Failed to retrieve checksums. This may be due to transient network problems, so try again later. Exiting.")
        
        self.verifyGPGSignature()
        
        # extract desired shasum line, remove extra junk from filepath/name.
        if os.path.isfile(self.sigFilename):
            print("Using existing shasum file.\n")
        else:
            os.system("cat SHA512SUMS | grep linux-" + self.options.arch + " | grep en-US | grep " + package + " | grep 'tar\.bz2\|xz' | grep -v sdk | awk '{gsub(\".*\",\"" + self.packageFilename + "\",$2); print $0}' > " + self.sigFilename)
            
        os.remove('SHA512SUMS')
        os.remove('SHA512SUMS.asc')

    def verifyMD5Sum(self):
        print("\nVerifying checksum\n")
        returncode = os.system("sha512sum -c " + self.sigFilename)
        if returncode:
            print("Checksum verification failed. This is most likely due to a corrupt download. You should delete files '", self.sigFilename, "' and '", self.packageFilename, "' and run the script again.\n")
            print("Would you like to delete those two files now? [y/n]? ")
            self.askyesno()
            if self.ans == 'y':
                print("\nOK, deleting files and exiting.\n")
                os.remove(self.packageFilename)
                os.remove(self.sigFilename)
            else:
                print("OK, exiting without deleting files.\n")
            sys.exit(1)


    def createDebStructure(self):
        provides = 'gnome-www-browser, www-browser, '
        if self.options.package == 'thunderbird':
            provides = 'mail-reader, '
        
        self.util.execSystemCommand(executionstring="sudo rm -rf " + self.debdir)
        self.util.execSystemCommand(executionstring="mkdir -p " + self.debdir)
        self.util.execSystemCommand(executionstring="mkdir -p " + self.debdir + self.options.targetdir)
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'usr','bin'))
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'usr','share','applications'))
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'usr','share','pixmaps'))
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'DEBIAN'))
        
        # to push out the new repository signing key, need to upgrade from DSA so we can use SHA2 and stop the weak digest warnings.
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'etc','apt','trusted.gpg.d'))
        self.util.execSystemCommand(executionstring="cp /etc/apt/trusted.gpg.d/ubuntuzilla.gpg " + os.path.join(self.debdir, 'etc','apt','trusted.gpg.d','ubuntuzilla.' + self.options.package + '.gpg'))
        
        os.chdir(os.path.join(self.debdir, 'DEBIAN'))
        open('control', 'w').write('''Package: ''' + self.packagename + '''
Version: ''' + self.releaseVersion + '''-0ubuntu''' + self.options.debversion + '''
Maintainer: ''' + self.version.author + ''' <''' + self.version.author_email + '''>
Architecture: ''' + self.debarch[self.options.arch] + '''
Provides: '''+ provides + self.options.package+'''
Description: Mozilla '''+self.options.package.capitalize()+''', official Mozilla build, packaged for Ubuntu by the Ubuntuzilla project.
 This is the unmodified Mozilla release binary of '''+self.options.package.capitalize()+''', packaged into a .deb by the Ubuntuzilla project.
 .
 It is strongly recommended that you back up your application profile data before installing, just in case. We really mean it!
 .
 Ubuntuzilla project homepage:
 ''' + self.version.url + '''
 .
 Mozilla project homepage:
 http://www.mozilla.com
''')
        # write the preinst and postrm scripts to divert /usr/bin/<package> links
        open('preinst', 'w').write('''#!/bin/sh
case "$1" in
    install)
        dpkg-divert --package ''' + self.packagename + ''' --add --divert /usr/bin/'''+self.options.package+'''.ubuntu --rename /usr/bin/'''+self.options.package+'''
    ;;
esac
''')

        open('postrm', 'w').write('''#!/bin/sh
case "$1" in
    remove|abort-install|disappear)
        dpkg-divert --package ''' + self.packagename + ''' --remove --divert /usr/bin/'''+self.options.package+'''.ubuntu --rename /usr/bin/'''+self.options.package+'''
    ;;
esac    
''')    
        self.util.execSystemCommand('chmod 755 preinst')
        self.util.execSystemCommand('chmod 755 postrm')
   
    def extractArchive(self):
        print("\nExtracting archive\n")
        if re.search(r'\.tar\.gz$', self.packageFilename):
            self.tar_flags = '-xzf'
        elif re.search(r'\.tar\.bz2$', self.packageFilename):
            self.tar_flags = '-xjf'
        elif re.search(r'\.tar\.xz$', self.packageFilename):
            self.tar_flags = '-xJf'

        #self.util.execSystemCommand(executionstring="sudo mkdir -p " + self.options.targetdir)
        #if not self.options.test:
        self.util.execSystemCommand(executionstring="sudo tar -C " + self.debdir + self.options.targetdir + " " + self.tar_flags + " /tmp/" + self.packageFilename)
        #else:
            # in testing mode, extract to /tmp.
        #    self.util.execSystemCommand(executionstring="sudo tar -C " + '/tmp' + " " + self.tar_flags + " " + self.packageFilename, includewithtest=True)
        #os.remove(self.packageFilename)
        #if not self.options.skipgpg:
        #    os.remove(self.sigFilename)
    
    def createSymlinks(self):
        os.chdir(os.path.join(self.debdir, 'usr','bin'))
        self.util.execSystemCommand('sudo ln -s ' + os.path.join(self.options.targetdir, self.options.package, self.options.package) + ' ' + self.options.package)
    
    def createMenuItem(self):
                
        print("Creating Applications menu item for "+self.options.package.capitalize()+".\n")
        os.chdir(os.path.join(self.debdir, 'usr','share','applications'))
        menufilename = self.options.package + '-mozilla-build.desktop'
        menuitemfile = open(menufilename, "w+")
        menuitemfile.write('''[Desktop Entry]
Encoding=UTF-8
Name=Mozilla Build of ''' + self.options.package.capitalize() + '''
GenericName=''' + self.GenericName + '''
Comment=''' + self.Comment + '''
Exec=''' + self.options.package + ''' %u
Icon=''' + self.iconPath + '''
Terminal=false
X-MultipleArgs=false
StartupWMClass=''' + self.wmClass + '''
Type=Application
Categories=''' + self.Categories + '''
MimeType=''' + self.mimeType)
        menuitemfile.close()
        self.util.execSystemCommand(executionstring="sudo chown root:root " + menufilename)
        self.util.execSystemCommand(executionstring="sudo chmod 644 " + menufilename)
        
        os.chdir(os.path.join(self.debdir, 'usr','share','pixmaps'))
        self.util.execSystemCommand(executionstring="cp " + self.options.debdir + "/" + self.options.package + "-mozilla-build.png ./")
        
    
    def createDeb(self):
        os.chdir(os.path.join('/tmp',self.options.package + 'debbuild'))
        self.util.execSystemCommand('sudo chown -R root:root debian')
        self.util.execSystemCommand('dpkg-deb -Zgzip --build debian ' + self.options.debdir)
    
    def createRepository(self):
        print("Would you like to update the local repository with the package just created [y/n]? ")
        self.askyesno()
        if self.ans == 'y':
            os.chdir(self.options.debdir)
            self.util.execSystemCommand('reprepro -S web -P extra -A ' + self.debarch[self.options.arch] + ' -Vb ../mozilla-apt-repository includedeb all ./'+self.packagename+'_' + self.releaseVersion + '-0ubuntu' + self.options.debversion + '_' + self.debarch[self.options.arch] + '.deb')
        else:
            print("\nOK, not updating repository...\n")
    
    def syncRepository(self):
        print("Would you like to upload the repository updates to the server [y/n]? ")
        self.askyesno()
        if self.ans == 'y':
            os.chdir(self.options.debdir)
            self.util.execSystemCommand('rsync -avP -e ssh ../mozilla-apt-repository/* nanotube,ubuntuzilla@frs.sourceforge.net:/home/frs/project/u/ub/ubuntuzilla/mozilla/apt/')
        else:
            print("\nOK, not uploading repository to server...\n")
    
    def printSuccessMessage(self):
        print("\nThe new " + self.options.package.capitalize() + " version " + self.releaseVersion + " has been packaged successfully.")

    def cleanup(self):
        print("Would you like to KEEP the original files, and the deb structure, on your hard drive [y/n]? ")
        self.askyesno()
        if self.ans == 'n':
            self.util.execSystemCommand(executionstring="sudo rm -rf " + self.debdir)
            os.remove(os.path.join('/tmp',self.packageFilename))
            os.remove(os.path.join('/tmp',self.sigFilename))
        else:
            print("\nOK, exiting without deleting the working files. If you wish to delete them manually later, they are in /tmp, and in " + self.debdir + ".")
        
    def askyesno(self):
        if not self.options.unattended:
            while 1:
                self.ans = input("Please enter 'y' or 'n': ")
                if self.ans in ['y','Y','n','N']:
                    self.ans = self.ans.lower()
                    break
        else:
            self.ans = 'y'



class FirefoxInstaller(MozillaInstaller):
    '''This class works with the firefox package'
    '''
    def __init__(self,options):
        MozillaInstaller.__init__(self, options)

    def getLatestVersion(self):
        MozillaInstaller.getLatestVersion(self)
        self.releaseVersion = self.util.getSystemOutput(executionstring="wget -S --tries=5 -O - \"https://download.mozilla.org/?product=firefox-latest-ssl&os=linux64&lang=en-US\" 2>&1 | grep \"Location:\" -m 1", numlines=1, errormessage="Failed to retrieve the latest version of "+ self.options.package.capitalize())
        self.releaseVersion = re.search(r'releases/(([0-9]+\.)+[0-9]+)',self.releaseVersion).group(1)
        
    def downloadPackage(self): # done, self.packageFilename
        MozillaInstaller.downloadPackage(self)
        #self.packageFilename = self.options.package + "-" + self.releaseVersion + ".tar.gz"
        
        print("\nDownloading", self.options.package.capitalize(), "archive from the Mozilla site\n")
        
        self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 " + "%mirror%" + self.options.package + "/releases/" + self.releaseVersion + "/linux-" + self.options.arch + "/en-US/" + self.packageFilename, 'includewithtest':True})
    
    def createMenuItem(self):
        #self.iconPath = self.options.targetdir + "/" + self.options.package + "/icons/mozicon128.png"
        self.iconPath = self.options.package + "-mozilla-build"
        self.GenericName = "Browser"
        self.Comment = "Web Browser"
        self.wmClass = "firefox" # as determined by 'xprop WM_CLASS'
        self.Categories = "Network;WebBrowser;"
        self.mimeType = "text/html;text/xml;application/xhtml+xml;application/xml;application/rss+xml;application/rdf+xml;image/gif;image/jpeg;image/png;x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/ftp;x-scheme-handler/chrome;video/webm;application/x-xpinstall;"
        MozillaInstaller.createMenuItem(self)

class FirefoxESRInstaller(MozillaInstaller):
    '''This class works with the firefox package'
    '''
    def __init__(self,options):
        MozillaInstaller.__init__(self, options)

    def getLatestVersion(self):
        MozillaInstaller.getLatestVersion(self)
        self.releaseVersion = self.util.getSystemOutput(executionstring="wget -S --tries=5 -O - \"https://download.mozilla.org/?product=firefox-esr-latest&os=linux64&lang=en-US\" 2>&1 | grep \"Location:\" -m 1", numlines=1, errormessage="Failed to retrieve the latest version of "+ self.options.package.capitalize())
        self.releaseVersion = re.search(r'releases/(([0-9]+\.)+[0-9]+esr)',self.releaseVersion).group(1)
        
    def downloadPackage(self): # done, self.packageFilename
        # we are going to dynamically determine the package name
        print("Retrieving package name for Firefox ESR...")
        for mirror in self.options.mirrors:
            try:
                self.packageFilename = self.util.getSystemOutput(executionstring="curl --no-progress-meter " + mirror + "firefox/releases/" + self.releaseVersion + "/linux-" + self.options.arch + "/en-US/ | w3m -dump -T text/html | grep 'firefox.*tar.bz2' | awk '{print $2}'", numlines=1)
                print("Success!: " + self.packageFilename)
                break
            except SystemCommandExecutionError:
                print("Download error. Trying again, hoping for a different mirror.")
                time.sleep(2)
        else:
            print("Failed to retrieve package name. This may be due to transient network problems, so try again later. If the problem persists, please seek help on our website,", self.version.url)
            sys.exit(1)

        #self.packageFilename = self.options.package + "-" + self.releaseVersion + ".tar.gz"
        
        print("\nDownloading Firefox ESR archive from the Mozilla site\n")
        
        self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 " + "%mirror%" + 'firefox' + "/releases/" + self.releaseVersion + "/linux-" + self.options.arch + "/en-US/" + self.packageFilename, 'includewithtest':True})
    
    def extractArchive(self):
        MozillaInstaller.extractArchive(self)
        print(self.debdir)
        self.util.execSystemCommand(executionstring="sudo mv " + self.debdir + self.options.targetdir + "/firefox" + " " + self.debdir + self.options.targetdir + "/firefox-esr")

        
    def createMenuItem(self):
        #self.iconPath = self.options.targetdir + "/" + self.options.package + "/icons/mozicon128.png"
        self.iconPath = self.options.package + "-mozilla-build"
        self.GenericName = "Browser"
        self.Comment = "Web Browser"
        self.wmClass = "firefox" # as determined by 'xprop WM_CLASS'
        self.Categories = "Network;WebBrowser;"
        self.mimeType = "text/html;text/xml;application/xhtml+xml;application/xml;application/rss+xml;application/rdf+xml;image/gif;image/jpeg;image/png;x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/ftp;x-scheme-handler/chrome;video/webm;application/x-xpinstall;"
        
        print("Creating Applications menu item for Firefox ESR.\n")
        os.chdir(os.path.join(self.debdir, 'usr','share','applications'))
        menufilename = self.options.package + '-mozilla-build.desktop'
        menuitemfile = open(menufilename, "w+")
        menuitemfile.write('''[Desktop Entry]
Encoding=UTF-8
Name=Mozilla Build of Firefox ESR
GenericName=''' + self.GenericName + '''
Comment=''' + self.Comment + '''
Exec=firefox-esr %u
Icon=''' + self.iconPath + '''
Terminal=false
X-MultipleArgs=false
StartupWMClass=''' + self.wmClass + '''
Type=Application
Categories=''' + self.Categories + '''
MimeType=''' + self.mimeType)
        menuitemfile.close()
        self.util.execSystemCommand(executionstring="sudo chown root:root " + menufilename)
        self.util.execSystemCommand(executionstring="sudo chmod 644 " + menufilename)
        
        os.chdir(os.path.join(self.debdir, 'usr','share','pixmaps'))
        self.util.execSystemCommand(executionstring="cp " + self.options.debdir + "/" + self.options.package + "-mozilla-build.png ./")

        
    def createDebStructure(self):
        provides = 'gnome-www-browser, www-browser, '
        
        self.util.execSystemCommand(executionstring="sudo rm -rf " + self.debdir)
        self.util.execSystemCommand(executionstring="mkdir -p " + self.debdir)
        self.util.execSystemCommand(executionstring="mkdir -p " + self.debdir + self.options.targetdir)
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'usr','bin'))
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'usr','share','applications'))
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'usr','share','pixmaps'))
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'DEBIAN'))
        
        # to push out the new repository signing key, need to upgrade from DSA so we can use SHA2 and stop the weak digest warnings.
        self.util.execSystemCommand(executionstring="mkdir -p " + os.path.join(self.debdir, 'etc','apt','trusted.gpg.d'))
        self.util.execSystemCommand(executionstring="cp /etc/apt/trusted.gpg.d/ubuntuzilla.gpg " + os.path.join(self.debdir, 'etc','apt','trusted.gpg.d','ubuntuzilla.' + self.options.package + '.gpg'))
        
        os.chdir(os.path.join(self.debdir, 'DEBIAN'))
        open('control', 'w').write('''Package: firefox-esr-mozilla-build
Version: ''' + self.releaseVersion + '''-0ubuntu''' + self.options.debversion + '''
Maintainer: ''' + self.version.author + ''' <''' + self.version.author_email + '''>
Architecture: ''' + self.debarch[self.options.arch] + '''
Provides: '''+ provides + '''firefox
Description: Mozilla Firefox ESR, official Mozilla build, packaged for Ubuntu by the Ubuntuzilla project.
 This is the unmodified Mozilla release binary of Firefox ESR, packaged into a .deb by the Ubuntuzilla project.
 .
 It is strongly recommended that you back up your application profile data before installing, just in case. We really mean it! If you use Firefox ESR along with the mainline Firefox release, it is recommended to use separate profiles.
 .
 The binary is linked as /usr/bin/firefox-esr.
 .
 Ubuntuzilla project homepage:
 ''' + self.version.url + '''
 .
 Mozilla project homepage:
 http://www.mozilla.com
''')

    def createSymlinks(self):
        os.chdir(os.path.join(self.debdir, 'usr','bin'))
        self.util.execSystemCommand('sudo ln -s ' + os.path.join(self.options.targetdir, 'firefox-esr/firefox') + ' ' + 'firefox-esr')

        
class ThunderbirdInstaller(MozillaInstaller):
    '''This class works with the thunderbird package'
    '''
    def __init__(self,options):
        MozillaInstaller.__init__(self, options)

    def getLatestVersion(self):
        MozillaInstaller.getLatestVersion(self)
        self.releaseVersion = self.util.getSystemOutput(executionstring="wget -c --tries=20 --read-timeout=60 --waitretry=10 -q -nv -O - https://www.thunderbird.net/en-US/thunderbird/all/ |grep 'releasenotes' -m 1", numlines=1, errormessage="Failed to retrieve the latest version of "+ self.options.package.capitalize())
        self.releaseVersion = re.search(r'thunderbird/(.*)/releasenotes',self.releaseVersion).group(1)


    def downloadPackage(self): # done, self.packageFilename
        MozillaInstaller.downloadPackage(self)
        #self.packageFilename = self.options.package + "-" + self.releaseVersion + ".tar.gz"
        
        print("\nDownloading", self.options.package.capitalize(), "archive from the Mozilla site\n")
        
        self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 " + "%mirror%" + self.options.package + "/releases/" + self.releaseVersion + "/linux-" + self.options.arch + "/en-US/" + self.packageFilename, 'includewithtest':True})
    
    def createMenuItem(self):
        #self.iconPath = self.options.targetdir + "/" + self.options.package + "/chrome/icons/default/default256.png"
        self.iconPath = self.options.package + "-mozilla-build"
        self.GenericName = "Mail Client"
        self.Comment = "Read/Write Mail/News with Mozilla Thunderbird"
        self.wmClass = "thunderbird" # as determined by 'xprop WM_CLASS'
        self.Categories = "Network;Email;"
        self.mimeType = "x-scheme-handler/mailto;application/x-xpinstall;"
        MozillaInstaller.createMenuItem(self)

class SeamonkeyInstaller(MozillaInstaller):
    '''This class works with the seamonkey package'
    '''
    def __init__(self,options):
        MozillaInstaller.__init__(self, options)

    def getLatestVersion(self):
        MozillaInstaller.getLatestVersion(self)
        self.releaseVersion = self.util.getSystemOutput(executionstring="wget -c --tries=20 --read-timeout=60 --waitretry=10 -q -nv -O - http://www.seamonkey-project.org/ |grep 'org/releases/.*/linux.*en-US' -m 1", numlines=1, errormessage="Failed to retrieve the latest version of "+ self.options.package.capitalize())
        self.releaseVersion = re.search(r'org/releases/(([0-9]+\.)+[0-9]+)',self.releaseVersion).group(1)
    
    def downloadPackage(self): # done, self.packageFilename
        MozillaInstaller.downloadPackage(self, sm=True) # don't really need this since SM broke directory traverse on their release server
        # try to get SM package filename
        packagelink = self.util.getSystemOutput(executionstring="wget -c --tries=20 --read-timeout=60 --waitretry=10 -q -nv -O - http://www.seamonkey-project.org/ |grep 'org/releases/.*/linux.*en-US.*" + self.options.arch + "' -m 1", numlines=1, errormessage="Failed to retrieve link to the latest version of "+ self.options.package.capitalize())
        packagelink = re.search(r'(https.*bz2)',packagelink).group(1)
        print("Retrieving package from " + packagelink)
        self.packageFilename = os.path.basename(packagelink)
        
        print("\nDownloading", self.options.package.capitalize(), "archive from the Mozilla site\n")
        
        self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 " + packagelink, 'includewithtest':True})

    def getMD5Sum(self): # done, self.sigFilename
        self.sigFilename = "MD5SUMS.txt" # self.options.package + "-" + self.releaseVersion + ".checksums"
        print("\nDownloading Seamonkey MD5 sums from the Mozilla site\n")

        self.util.robustDownload(argsdict={'executionstring':"wget -c --tries=5 --read-timeout=20 --waitretry=10 -q -nv -O - " + "%mirror%" + "/releases/" + self.releaseVersion + "/" + self.sigFilename + " | grep -F 'linux-" + self.options.arch + "/en-US/" + self.packageFilename + "' | grep -F 'md5' > " + self.sigFilename, 'includewithtest':True}, errormsg="Failed to retrieve md5 sum. This may be due to transient network problems, so try again later. Exiting.")
        self.util.execSystemCommand("sed -i 's#md5.*linux-" + self.options.arch + "/en-US/##' " + self.sigFilename, includewithtest=True)

        # example: 91360c07aea125dbc3e03e33de4db01a  ./linux-i686/en-US/seamonkey-2.0.tar.bz2
        # sed to:  91360c07aea125dbc3e03e33de4db01a  ./seamonkey-2.0.tar.bz2

    def verifyMD5Sum(self):
        print("\nVerifying Seamonkey MD5 sum\n")
        returncode = os.system("md5sum -c " + self.sigFilename)
        if returncode:
            print("MD5 sum verification failed. This is most likely due to a corrupt download. You should delete files '", self.sigFilename, "' and '", self.packageFilename, "' and run the script again.\n")
            print("Would you like to delete those two files now? [y/n]? ")
            self.askyesno()
            if self.ans == 'y':
                print("\nOK, deleting files and exiting.\n")
                os.remove(self.packageFilename)
                os.remove(self.sigFilename)
            else:
                print("OK, exiting without deleting files.\n")
            sys.exit(1)

    def downloadGPGSignature(self): #don't need this for seamonkey, blank it out
        pass

    def getMozillaGPGKey(self): #don't need this for seamonkey, blank it out
        pass
        
    def verifyGPGSignature(self): #don't need this for seamonkey, blank it out
        pass
    
    def createMenuItem(self):
        #self.iconPath = self.options.targetdir + "/" + self.options.package + "/chrome/icons/default/" + self.options.package + ".png"
        self.iconPath = self.options.package + "-mozilla-build"
        self.GenericName = "Internet Suite"
        self.Comment = "Web Browser, Email/News Client, HTML Editor, IRC Client"
        self.wmClass = "SeaMonkey" # as determined by 'xprop WM_CLASS'
        self.Categories = "Network;WebBrowser;Email;WebDevelopment;IRCClient;"
        self.mimeType = "text/html;text/xml;application/xhtml+xml;application/xml;application/rss+xml;application/rdf+xml;image/gif;image/jpeg;image/png;x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/ftp;x-scheme-handler/chrome;video/webm;application/x-xpinstall;x-scheme-handler/mailto;"
        MozillaInstaller.createMenuItem(self)
        
if __name__ == '__main__':
    
    bs = BaseStarter()
    bs.start()
