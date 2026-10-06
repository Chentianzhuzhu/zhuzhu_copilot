; *** Inno Setup version 6.5.0+ English messages ***
;
; Standard English language file for Inno Setup.
; This file is included because the bundled Default.isl in this Inno Setup
; build has been localized to Chinese Simplified.
;
[LangOptions]
LanguageName=English
LanguageID=$0409
LanguageCodePage=0
[Messages]
; *** Application titles
SetupAppTitle=Setup
SetupWindowTitle=Setup - %1
UninstallAppTitle=Uninstall
UninstallAppFullTitle=%1 Uninstall
; *** Misc. common
InformationTitle=Information
ConfirmTitle=Confirm
ErrorTitle=Error
; *** SetupLdr messages
SetupLdrStartupMessage=This will install %1. Do you wish to continue?
LdrCannotCreateTemp=Cannot create temporary file. Setup aborted.
LdrCannotExecTemp=Cannot execute file in the temporary directory. Setup aborted.
HelpTextNote=
; *** Startup error messages
LastErrorMessage=%1.%n%nError %2: %3
SetupFileMissing=The file %1 is missing from the installation directory. Please correct this problem or obtain a new copy of the program.
SetupFileCorrupt=The installation files are corrupted. Please obtain a new copy of the program.
SetupFileCorruptOrWrongVer=The installation files are corrupted, or are incompatible with this version of Setup. Please correct this problem or obtain a new copy of the program.
InvalidParameter=Invalid command line parameter:%n%n%1
SetupAlreadyRunning=Setup is already running.
WindowsVersionNotSupported=This program does not support the version of Windows your computer is running.
WindowsServicePackRequired=This program requires %1 Service Pack %2 or later.
NotOnThisPlatform=This program will not run on %1.
OnlyOnThisPlatform=This program will only run on %1.
OnlyOnTheseArchitectures=This program can only be installed on versions of Windows designed for the following processor architectures:%n%n%1
WinVersionTooLowError=This program requires %1 version %2 or later.
WinVersionTooHighError=This program cannot be installed on %1 version %2 or later.
AdminPrivilegesRequired=You must be logged in as an administrator when installing this program.
PowerUserPrivilegesRequired=You must be logged in as an administrator or power user when installing this program.
SetupAppRunningError=Setup has detected that %1 is currently running.%n%nPlease close all instances of it now, then click OK to continue, or Cancel to exit.
UninstallAppRunningError=Uninstall has detected that %1 is currently running.%n%nPlease close all instances of it now, then click OK to continue, or Cancel to exit.
; *** Startup questions
PrivilegesRequiredOverrideTitle=Choose Install Mode
PrivilegesRequiredOverrideInstruction=Choose an install mode
PrivilegesRequiredOverrideText1=%1 can be installed for all users (requires admin privileges), or just for you.
PrivilegesRequiredOverrideText2=%1 can be installed just for you, or for all users (requires admin privileges).
PrivilegesRequiredOverrideAllUsers=Install for all users (&A)
PrivilegesRequiredOverrideAllUsersRecommended=Install for all users (&A) (recommended)
PrivilegesRequiredOverrideCurrentUser=Install just for me (&M)
PrivilegesRequiredOverrideCurrentUserRecommended=Install just for me (&M) (recommended)
; *** Misc. errors
ErrorCreatingDir=Setup was unable to create the directory "%1".
ErrorTooManyFilesInDir=Unable to create a file in the directory "%1" because it contains too many files.
; *** Setup common messages
ExitSetupTitle=Exit Setup
ExitSetupMessage=Setup has not finished installing %1.%n%nIf you exit now, the program will not be installed.%n%nYou can run Setup again later to complete the installation.%n%nExit Setup?
AboutSetupMenuItem=&About Setup...
AboutSetupTitle=About Setup
AboutSetupMessage=%1 version %2%n%3%n%n%1 home page:%n%4
AboutSetupNote=
TranslatorNote=
; *** Buttons
ButtonBack=< &Back
ButtonNext=&Next >
ButtonInstall=&Install
ButtonOK=OK
ButtonCancel=Cancel
ButtonYes=&Yes
ButtonYesToAll=Yes to &All
ButtonNo=&No
ButtonNoToAll=No to &All
ButtonFinish=&Finish
ButtonBrowse=&Browse...
ButtonWizardBrowse=Browse...
ButtonNewFolder=&Make New Folder
; *** "Select Language" dialog messages
SelectLanguageTitle=Select Setup Language
SelectLanguageLabel=Select the language to use during installation:
; *** Common wizard text
ClickNext=Click Next to continue, or Cancel to exit Setup.
BeveledLabel=
BrowseDialogTitle=Browse
BrowseDialogLabel=Select a folder from the list below, then click OK.
NewFolderName=New Folder
; *** "Welcome" wizard page
WelcomeLabel1=Welcome to the [name] Setup Wizard
WelcomeLabel2=This will install [name/ver] on your computer.%n%nIt is recommended that you close all other applications before continuing.
; *** "Password" wizard page
WizardPassword=Password
PasswordLabel1=This installation is password protected.
PasswordLabel3=Please enter the password, then click Next to continue. Passwords are case-sensitive.
PasswordEditLabel=&Password:
IncorrectPassword=The password you entered is incorrect. Please try again.
; *** "License Agreement" wizard page
WizardLicense=License Agreement
LicenseLabel=Please read the following important information before continuing.
LicenseLabel3=Please read the following License Agreement. You must accept the terms of this agreement before continuing with the installation.
LicenseAccepted=I &accept the agreement
LicenseNotAccepted=I &do not accept the agreement
; *** "Information" wizard pages
WizardInfoBefore=Information
InfoBeforeLabel=Please read the following important information before continuing.
InfoBeforeClickLabel=When you are ready to continue, click Next.
WizardInfoAfter=Information
InfoAfterLabel=Please read the following important information before continuing.
InfoAfterClickLabel=When you are ready to continue, click Next.
; *** "User Information" wizard page
WizardUserInfo=User Information
UserInfoDesc=Please enter your information.
UserInfoName=&User Name:
UserInfoOrg=&Organization:
UserInfoSerial=&Serial Number:
UserInfoNameRequired=Please enter your name.
; *** "Select Destination Location" wizard page
WizardSelectDir=Select Destination Location
SelectDirDesc=Where should [name] be installed?
SelectDirLabel3=Setup will install [name] into the following folder.
SelectDirBrowseLabel=To continue, click Next. If you would like to select a different folder, click Browse.
DiskSpaceGBLabel=At least [gb] GB of free disk space is required.
DiskSpaceMBLabel=At least [mb] MB of free disk space is required.
CannotInstallToNetworkDrive=Setup cannot install to a network drive.
CannotInstallToUNCPath=Setup cannot install to a UNC path.
InvalidPath=You must enter a full path with drive letter; for example:%n%nC:\APP%n%nor a UNC path such as:%n%n\\server\share
InvalidDrive=The drive or UNC share you selected does not exist or is not accessible. Please select another.
DiskSpaceWarningTitle=Out of disk space
DiskSpaceWarning=Setup requires at least %1 KB of free disk space to install %2, but the selected drive only has %3 KB available.%n%nDo you want to continue with the installation anyway?
DirNameTooLong=Folder name or path too long.
InvalidDirName=Folder name is invalid.
BadDirName32=Folder name cannot contain the following characters:%n%n%1
DirExistsTitle=Folder exists
DirExists=The folder:%n%n%1%n%nalready exists. Would you like to install to this folder anyway?
DirDoesntExistTitle=Folder does not exist
DirDoesntExist=The folder:%n%n%1%n%ndoes not exist. Would you like Setup to create it?
; *** "Select Components" wizard page
WizardSelectComponents=Select Components
SelectComponentsDesc=Which components should be installed?
SelectComponentsLabel2=Select the components you want to install; clear the components you do not want to install. Click Next when you are ready to continue.
FullInstallation=Full installation
; if possible don't translate 'Compact' as 'Minimal' (I mean 'Minimal' in your language)
CompactInstallation=Compact installation
CustomInstallation=Custom installation
NoUninstallWarningTitle=Component already exists
NoUninstallWarning=Setup has detected that the following components are already installed on your computer:%n%n%1%n%nClearing a component's check box will not uninstall it.%n%nDo you want to continue?
ComponentSize1=%1 KB
ComponentSize2=%1 MB
ComponentsDiskSpaceGBLabel=The selected components require at least [gb] GB of disk space.
ComponentsDiskSpaceMBLabel=The selected components require at least [mb] MB of disk space.
; *** "Select Additional Tasks" wizard page
WizardSelectTasks=Select Additional Tasks
SelectTasksDesc=Which additional tasks should be performed?
SelectTasksLabel2=Select the additional tasks you would like Setup to perform while installing [name], then click Next.
; *** "Select Start Menu Folder" wizard page
WizardSelectProgramGroup=Select Start Menu Folder
SelectStartMenuFolderDesc=Where should Setup place the program's shortcuts?
SelectStartMenuFolderLabel3=Setup will create the program's shortcuts in the following Start Menu folder.
SelectStartMenuFolderBrowseLabel=To continue, click Next. If you would like to select a different folder, click Browse.
MustEnterGroupName=You must enter a folder name.
GroupNameTooLong=Folder name or path too long.
InvalidGroupName=Folder name is invalid.
BadGroupName=Folder name cannot contain the following characters:%n%n%1
NoProgramGroupCheck2=Don't create a Start Menu folder
; *** "Ready to Install" wizard page
WizardReady=Ready to Install
ReadyLabel1=Setup is now ready to begin installing [name] on your computer.
ReadyLabel2a=Click Install to continue with the installation, or click Back if you want to review or change any settings.
ReadyLabel2b=Click Install to continue with the installation.
ReadyMemoUserInfo=User Information:
ReadyMemoDir=Destination Location:
ReadyMemoType=Setup Type:
ReadyMemoComponents=Selected Components:
ReadyMemoGroup=Start Menu Folder:
ReadyMemoTasks=Additional Tasks:
; *** TDownloadWizardPage wizard page and DownloadTemporaryFile
DownloadingLabel2=Downloading files...
ButtonStopDownload=Stop &download
StopDownload=Are you sure you want to stop the download?
ErrorDownloadAborted=Download aborted.
ErrorDownloadFailed=Download failed: %1 %2.
ErrorDownloadSizeFailed=Failed to get size: %1 %2.
ErrorProgress=Invalid progress: %1 / %2.
ErrorFileSize=Invalid file size: expected %1, got %2.
; *** TExtractionWizardPage wizard page and ExtractArchive
ExtractingLabel=Extracting files...
ButtonStopExtraction=Stop &extract
StopExtraction=Are you sure you want to stop extracting?
ErrorExtractionAborted=Extraction aborted.
ErrorExtractionFailed=Extraction failed: %1
; *** Archive extraction failure details
ArchiveIncorrectPassword=Incorrect password.
ArchiveIsCorrupted=Archive is corrupted.
ArchiveUnsupportedFormat=Unsupported archive format.
; *** "Preparing to Install" wizard page
WizardPreparing=Preparing to Install
PreparingDesc=Setup is preparing to install [name] on your computer.
PreviousInstallNotCompleted=A previous version of this application has not completed installation/uninstallation. You must restart your computer to complete the previous installation.%n%nAfter restarting your computer, run Setup again to finish installing [name].
CannotContinue=Setup cannot continue. Click Cancel to exit.
ApplicationsFound=The following applications are using files that need to be updated by Setup. It is recommended that you allow Setup to automatically close these applications.
ApplicationsFound2=The following applications are using files that need to be updated by Setup. It is recommended that you allow Setup to automatically close these applications. Setup will attempt to restart the applications after the installation has completed.
CloseApplications=Automatically close the applications
DontCloseApplications=Do not close the applications
ErrorCloseApplications=Setup was unable to automatically close all applications. It is recommended that you close all applications using files that need to be updated by Setup before continuing.
PrepareToInstallNeedsRestart=Setup must restart your computer. After restarting, please run Setup again to complete the installation of [name].%n%nWould you like to restart now?
; *** "Installing" wizard page
WizardInstalling=Installing
InstallingLabel=Please wait while Setup installs [name] on your computer.
; *** "Setup Completed" wizard page
FinishedHeadingLabel=Completing the [name] Setup Wizard
FinishedLabelNoIcons=Setup has finished installing [name] on your computer.
FinishedLabel=Setup has finished installing [name] on your computer. The application may be launched by selecting the installed shortcuts.
ClickFinish=Click Finish to exit Setup.
FinishedRestartLabel=To complete the installation of [name], Setup must restart your computer. Would you like to restart now?
FinishedRestartMessage=To complete the installation of [name], Setup must restart your computer.%n%nWould you like to restart now?
ShowReadmeCheck=Yes, I would like to view the Readme file
YesRadio=Yes, restart the computer now (&Y)
NoRadio=No, I will restart the computer later (&N)
; used for example as 'Run MyProg.exe'
RunEntryExec=Run %1
; used for example as 'View Readme.txt'
RunEntryShellExec=View %1
; *** "Setup Needs the Next Disk" stuff
ChangeDiskTitle=Setup Needs the Next Disk
SelectDiskLabel2=Please insert disk %1 and click OK.%n%nIf the files on this disk can be found in a folder other than the one displayed below, enter the correct path or click Browse.
PathLabel=&Path:
FileNotInDir2=File "%1" not found in "%2". Please insert the correct disk or select another folder.
SelectDirectoryLabel=Please specify where the next disk is located.
; *** Installation phase messages
SetupAborted=Setup was not completed successfully.%n%nPlease correct the problem and run Setup again.
AbortRetryIgnoreSelectAction=Select Action
AbortRetryIgnoreRetry=&Retry
AbortRetryIgnoreIgnore=&Ignore and continue
AbortRetryIgnoreCancel=Cancel Setup
RetryCancelSelectAction=Select Action
RetryCancelRetry=&Retry
RetryCancelCancel=Cancel
; *** Installation status messages
StatusClosingApplications=Closing applications...
StatusCreateDirs=Creating directories...
StatusExtractFiles=Extracting files...
StatusDownloadFiles=Downloading files...
StatusCreateIcons=Creating shortcuts...
StatusCreateIniEntries=Creating INI entries...
StatusCreateRegistryEntries=Creating registry entries...
StatusRegisterFiles=Registering files...
StatusSavingUninstall=Saving uninstall information...
StatusRunProgram=Finishing installation...
StatusRestartingApplications=Restarting applications...
StatusRollback=Rolling back changes...
; *** Misc. errors
ErrorInternal2=Internal error: %1.
ErrorFunctionFailedNoCode=%1 failed.
ErrorFunctionFailed=%1 failed; code %2.
ErrorFunctionFailedWithMessage=%1 failed; code %2.%n%3
ErrorExecutingProgram=Unable to execute file:%n%1
; *** Registry errors
ErrorRegOpenKey=Error opening registry key:%n%1\%2
ErrorRegCreateKey=Error creating registry key:%n%1\%2
ErrorRegWriteKey=Error writing registry key:%n%1\%2
; *** INI errors
ErrorIniEntry=Error creating INI entry in file "%1".
; *** File copying errors
FileAbortRetryIgnoreSkipNotRecommended=&Skip this file (not recommended)
FileAbortRetryIgnoreIgnoreNotRecommended=&Ignore and continue (not recommended)
SourceIsCorrupted=The source file is corrupted.
SourceDoesntExist=The source file "%1" does not exist.
SourceVerificationFailed=Source file verification failed: %1
VerificationSignatureDoesntExist=The signature file "%1" does not exist.
VerificationSignatureInvalid=The signature file "%1" is invalid.
VerificationKeyNotFound=The signature file "%1" uses an unknown key.
VerificationFileNameIncorrect=The file name is incorrect.
VerificationFileTagIncorrect=The file tag is incorrect.
VerificationFileSizeIncorrect=The file size is incorrect.
VerificationFileHashIncorrect=The file hash is incorrect.
ExistingFileReadOnly2=Unable to replace existing file. It is write-protected.
ExistingFileReadOnlyRetry=Remove the read-only attribute and retry (&R)
ExistingFileReadOnlyKeepExisting=Keep the existing file (&K)
ErrorReadingExistingDest=Error trying to read the existing file:
FileExistsSelectAction=Select Action
FileExists2=The file already exists.
FileExistsOverwriteExisting=Overwrite the existing file (&O)
FileExistsKeepExisting=Keep the existing file (&K)
FileExistsOverwriteOrKeepAll=Do this for the next conflict files (&D)
ExistingFileNewerSelectAction=Select Action
ExistingFileNewer2=The existing file is newer than the file Setup is about to install.
ExistingFileNewerOverwriteExisting=Overwrite the existing file (&O)
ExistingFileNewerKeepExisting=Keep the existing file (&K) (recommended)
ExistingFileNewerOverwriteOrKeepAll=Do this for the next conflict files (&D)
ErrorChangingAttr=Error trying to change the attributes of the existing file:
ErrorCreatingTemp=Error trying to create a file in the destination directory:
ErrorReadingSource=Error trying to read the source file:
ErrorCopying=Error trying to copy the file:
ErrorDownloading=Error trying to download the file:
ErrorExtracting=Error trying to extract the archive:
ErrorReplacingExistingFile=Error trying to replace the existing file:
ErrorRestartReplace=RestartReplace failed:
ErrorRenamingTemp=Error trying to rename a file in the destination directory:
ErrorRegisterServer=Unable to register DLL/OCX: %1
ErrorRegSvr32Failed=RegSvr32 failed with exit code %1.
ErrorRegisterTypeLib=Unable to register type library: %1
; *** Uninstall display name markings
; used for example as 'My Program (32-bit)'
UninstallDisplayNameMark=%1 (%2)
; used for example as 'My Program (32-bit, All users)'
UninstallDisplayNameMarks=%1 (%2, %3)
UninstallDisplayNameMark32Bit=32-bit
UninstallDisplayNameMark64Bit=64-bit
UninstallDisplayNameMarkAllUsers=All users
UninstallDisplayNameMarkCurrentUser=Current user
; *** Post-install errors
ErrorOpeningReadme=Error trying to open the readme file.
ErrorRestartingComputer=Setup was unable to restart your computer. Please restart it manually.
; *** Uninstall messages
UninstallNotFound=File "%1" does not exist. Cannot uninstall.
UninstallOpenError=File "%1" cannot be opened. Cannot uninstall.
UninstallUnsupportedVer=This version of Uninstall is not recognized by the uninstall log file "%1". Cannot uninstall.
UninstallUnknownEntry=An unknown entry (%1) was encountered in the uninstall log.
ConfirmUninstall=Are you sure you want to completely remove %1 and all of its components?
UninstallOnlyOnWin64=This installation can only be uninstalled on 64-bit Windows.
OnlyAdminCanUninstall=This installation can only be uninstalled by a user with administrator privileges.
UninstallStatusLabel=Please wait while %1 is removed from your computer.
UninstalledAll=%1 was successfully removed from your computer.
UninstalledMost=%1 uninstall complete.%n%nSome elements could not be removed. These can be removed manually.
UninstalledAndNeedsRestart=To complete the uninstallation of %1, your computer must be restarted.%n%nWould you like to restart now?
UninstallDataCorrupted=The file "%1" is corrupted. Cannot uninstall.
; *** Uninstall status messages
ConfirmDeleteSharedFileTitle=Delete shared file?
ConfirmDeleteSharedFile2=The following shared file is no longer used by any program. Would you like Uninstall to delete it?%n%nIf the file is deleted but a program still uses it, the program may no longer function correctly. If you are unsure, select No. Leave the file on the system to avoid problems.
SharedFileNameLabel=File name:
SharedFileLocationLabel=Location:
WizardUninstalling=Uninstall Status
StatusUninstalling=Uninstalling %1...
; *** Shutdown block reasons
ShutdownBlockReasonInstallingApp=Installing %1.
ShutdownBlockReasonUninstallingApp=Uninstalling %1.
; The custom messages below aren't used by Setup itself, but if you make
; use of them in your scripts, you'll want to translate them.
[CustomMessages]
NameAndVersion=%1 version %2
AdditionalIcons=Additional icons:
CreateDesktopIcon=Create a &desktop icon
CreateQuickLaunchIcon=Create a &Quick Launch icon
ProgramOnTheWeb=%1 on the Web
UninstallProgram=Uninstall %1
LaunchProgram=Launch %1
AssocFileExtension=&Associate %2 file extension with %1
AssocingFileExtension=Associating %2 file extension with %1...
AutoStartProgramGroupDescription=Startup group:
AutoStartProgram=Start %1 automatically
AddonHostProgramNotFound=%1 could not find the folder you selected.%n%nWould you like to continue?
